from abc import ABCMeta, abstractmethod
import numpy as np
import scipy.linalg

from operator_tools import (transition_operator, operator_extend, unit_vec,
                            tensor, extend_vib_operator, vib_create,
                            vib_annihilate)
from polarization import polarization_vector
from utils import imemoize, memoized_property


class HamiltonianError(Exception):
    """
    Error class for Hamiltonian errors
    """

class Hamiltonian(object):
    """
    Parent class for Hamiltonian objects
    """
    __metaclass__ = ABCMeta

    @abstractmethod
    def H(self, subspace):
        """
        Returns the system Hamiltonian in the given Hilbert subspace as a matrix
        """

    @abstractmethod
    def ground_state(self, subspace):
        """
        Returns the ground electronic state of this Hamiltonian as a density
        operator
        """

    @abstractmethod
    def in_rotating_frame(self, rw_freq=None):
        """
        Returns a new Hamiltonian shifted to the rotating frame at the given
        frequency

        By default, sets the rotating frame to the central frequency.
        """

    @abstractmethod
    def dipole_operator(self, subspace='gef', polarization='x',
                        transitions='-+'):
        """
        Return the matrix representation in the given subspace of the requested
        dipole operator
        """

    @abstractmethod
    def system_bath_couplings(self, subspace='gef'):
        """
        Return a list of matrix representations in the given subspace of the
        system-bath coupling operators
        """

    def n_states(self, subspace):
        return len(self.H(subspace))

    @imemoize
    def eig(self, subspace):
        """
        Returns the eigensystem solution E, U for the system part of this
        Hamiltonian in the given subspace
        """
        E, U = scipy.linalg.eigh(self.H(subspace))
        return (E, U)

    def E(self, subspace):
        """
        Returns the eigen-energies of the system part of this Hamiltonian in the
        given subspace
        """
        return self.eig(subspace)[0]

    def U(self, subspace):
        """
        Returns the matrix which transform the system part of this Hamiltonian
        from the site to the energy eigen-basis.
        """
        return self.eig(subspace)[1]

    @property
    def mean_excitation_freq(self):
        """
        Average excited state transition energy
        """
        return np.mean(self.E('e')) + self.energy_offset

    @property
    def freq_step(self):
        """
        An appropriate sampling rate, according to the Nyquist theorem, so that
        all frequencies of the Hamiltonian can be resolved

        Note: If this frequency is very high, you probably need to transform to
        the rotating frame first.
        """
        freq_span = self.E('gef').max() - self.E('gef').min()
        return 2 * (freq_span + self.energy_spread_extra)

    @property
    def time_step(self):
        return 1.0 / self.freq_step


class ElectronicHamiltonian(Hamiltonian):
    """
    Hamiltonian for an electronic system with coupling to an external field
    and an identical bath at each pigment

    Properties
    ----------
    H_1exc : np.ndarray
        Matrix representation of this hamiltonian in the 1-excitation subspace
    energy_offset : number, optional
        Constant energy offset of the diagonal entries in H_1exc from the ground
        state energy.
    bath : bath.Bath, optional
        Object containing the bath information (i.e., correlation function and
        temperature). Each site is assumed to be linearly coupled to an
        identical bath of this form.
    dipoles : np.ndarray, optional
        n x 3 array of dipole moments for each site.
    energy_spread_extra : float, optional (default 100)
        Default extra frequency to add to the spread of energies when
        determining the frequency step size automatically.
    """
    def __init__(self, H_1exc, energy_offset=0, bath=None, dipoles=None,
                 energy_spread_extra=100.0):
        self.H_1exc = np.asanyarray(H_1exc)
        self.energy_offset = energy_offset
        self.bath = bath
        self.dipoles = np.asanyarray(dipoles) if dipoles is not None else None
        self.energy_spread_extra = energy_spread_extra
        self.n_vibrational_states = 1

    @property
    def n_sites(self):
        return len(self.H_1exc)

    @imemoize
    def H(self, subspace):
        """
        Returns the system Hamiltonian in the given Hilbert subspace as a matrix
        """
        return operator_extend(self.H_1exc, subspace)

    @imemoize
    def ground_state(self, subspace):
        """
        Returns the ground electronic state of this Hamiltonian as a density
        operator
        """
        N = self.n_states(subspace)
        state = np.zeros((N, N), dtype=complex)
        if 'g' in subspace:
            state[0, 0] = 1.0
        return state

    @imemoize
    def in_rotating_frame(self, rw_freq=None):
        """
        Returns a new Hamiltonian shifted to the rotating frame at the given
        frequency

        By default, sets the rotating frame to the central frequency.
        """
        if rw_freq is None:
            rw_freq = self.mean_excitation_freq
        H_1exc = self.H_1exc - ((rw_freq - self.energy_offset)
                                * np.identity(len(self.H_1exc)))
        return type(self)(H_1exc, rw_freq, self.bath, self.dipoles,
                          self.energy_spread_extra)

    def dipole_operator(self, subspace='gef', polarization='x',
                        transitions='-+'):
        """
        Return the matrix representation in the given subspace of the requested
        dipole operator
        """
        if self.dipoles is None:
            raise HamiltonianError('transition dipole moments undefined')
        trans_ops = [transition_operator(n, self.n_sites, subspace, transitions)
                     for n in xrange(self.n_sites)]
        return np.einsum('nij,nk,k->ij', trans_ops, self.dipoles,
                         polarization_vector(polarization))

    def number_operator(self, site, subspace='gef'):
        """
        Returns the number operator a_n^\dagger a_n for site n
        """
        return operator_extend(
            np.diag(unit_vec(site, self.n_sites, dtype=float)), subspace)

    def system_bath_couplings(self, subspace='gef'):
        """
        Return a list of matrix representations in the given subspace of the
        system-bath coupling operators
        """
        if self.bath is None:
            raise HamiltonianError('bath undefined')
        return [self.number_operator(n, subspace) for n in xrange(self.n_sites)]


def thermal_state(hamiltonian_matrix, temperature):
    rho = scipy.linalg.expm(-hamiltonian_matrix / temperature)
    return rho / np.trace(rho)


class VibronicHamiltonian(Hamiltonian):
    """
    Hamiltonian which extends an electronic Hamiltonian to include explicit
    vibrations

    Properties
    ----------
    electronic : ElectronicHamiltonian
        Object which represents the electronic part of the Hamiltonian,
        including its bath.
    n_vibrational_levels : np.ndarray
        Array giving the number of energy levels to include with each
        vibration.
    vib_energies : np.ndarray
        Array giving the energies of the vibrational modes.
    elec_vib_couplings : np.ndarray
        2D array giving the electronic-vibrational couplings [c_{nm}], where
        the coupling operators are in the form:
        c_{nm}*|n><n|*(b(m) + b(m)^\dagger),
        where |n> is the singly excited electronic state of site n in the full
        singly excited subspace, and b(m) and b(m)^\dagger are the
        vibrational annihilation and creation operators for vibration m.
    """
    def __init__(self, electronic, n_vibrational_levels, vib_energies,
                 elec_vib_couplings):
        self.electronic = electronic
        self.energy_offset = self.electronic.energy_offset
        self.energy_spread_extra = self.electronic.energy_spread_extra
        self.bath = self.electronic.bath
        self.n_sites = self.electronic.n_sites
        self.n_vibrational_levels = np.asanyarray(n_vibrational_levels)
        self.vib_energies = np.asanyarray(vib_energies)
        self.elec_vib_couplings = np.asanyarray(elec_vib_couplings)

    @memoized_property
    def n_vibrational_states(self):
        """
        Returns the total number of vibrational states in the full vibrational
        subspace (i.e. the dimension of the full vibrational subspace)
        """
        return np.prod(self.n_vibrational_levels)

    @memoized_property
    def H_vibrational(self):
        """
        Returns the Hamiltonian of the vibrations included explicitly in this
        model
        """
        H_vib = np.diag(np.zeros(self.n_vibrational_states))
        for m, (num_levels, vib_energy) in \
                enumerate(zip(self.n_vibrational_levels, self.vib_energies)):
            vib_operator = np.diag(np.arange(num_levels))
            H_vib += (vib_energy
                      * extend_vib_operator(self.n_vibrational_levels, m,
                                            vib_operator))
        return H_vib

    def H_electronic_vibrational(self, subspace='gef'):
        """
        Returns the electronic-vibrational coupled part of the Hamiltonian,
        given by
        H_{el-vib} = sum_{n,m} c_{nm}*|n><n|*(b(m) + b(m)^\dagger)
        where |n> is the singly excited electronic state of site n in the full
        singly excited subspace, and b(m) and b(m)^\dagger are the
        annihilation and creation operators for vibrational mode m
        """
        H_el_vib = np.diag(np.zeros(self.electronic.n_states(subspace)
                                    * self.n_vibrational_states))
        for i in np.arange(self.electronic.n_sites):
            el_operator = self.electronic.number_operator(i, subspace)
            for m, num_levels in enumerate(self.n_vibrational_levels):
                vib_operator = (vib_annihilate(num_levels)
                                + vib_create(num_levels))
                H_el_vib += (self.elec_vib_couplings[i, m]
                             * tensor(el_operator,
                                      extend_vib_operator(
                                          self.n_vibrational_levels,
                                          m, vib_operator)))
        return H_el_vib

    @imemoize
    def H(self, subspace='gef'):
        """
        Returns the matrix representation of the system Hamiltonian in the
        given electronic subspace
        """
        return (self.el_to_sys_operator(self.electronic.H(subspace))
                + self.vib_to_sys_operator(self.H_vibrational, subspace)
                + self.H_electronic_vibrational(subspace))

    @imemoize
    def ground_state(self, subspace='gef'):
        return np.kron(self.electronic.ground_state(subspace),
                       thermal_state(self.H_vibrational,
                                     self.bath.temperature))

    @imemoize
    def in_rotating_frame(self, *args, **kwargs):
        """
        Returns a new Hamiltonian shifted to the rotating frame at the given
        frequency

        By default, sets the rotating frame to the central frequency.
        """
        return type(self)(self.electronic.in_rotating_frame(*args, **kwargs),
                          self.n_vibrational_levels, self.vib_energies,
                          self.elec_vib_couplings)

    def el_to_sys_operator(self, el_operator):
        """
        Extends the electronic operator el_operator, which may be in an
        electronic subspace, into a system operator in that subspace
        """
        return tensor(el_operator, np.eye(self.n_vibrational_states))

    def vib_to_sys_operator(self, vib_operator, subspace='gef'):
        """
        Extends the vibrational operator vib_operator, which may be in a
        vibrational subspace, into a system operator in that subspace
        and in the given electronic subspace
        """
        return tensor(np.eye(self.electronic.n_states(subspace)),
                             vib_operator)

    def dipole_operator(self, *args, **kwargs):
        """
        Return the matrix representation in the given subspace of the requested
        dipole operator
        """
        return self.el_to_sys_operator(self.electronic.
                                       dipole_operator(*args, **kwargs))

    def system_bath_couplings(self, *args, **kwargs):
        """
        Return a list of matrix representations in the given subspace of the
        system-bath coupling operators
        """
        return self.el_to_sys_operator(self.electronic.
                                       system_bath_couplings(*args, **kwargs))