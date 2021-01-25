import math
from typing import Tuple

import gpflow
import numpy as np
import tensorflow as tf
import gpflow.config as config

from src.kernels.base import ContinuousDiscreteModel, SDEKernelMixin

class RBF(gpflow.kernels.RBF, SDEKernelMixin):
    __doc__ = gpflow.kernels.RBF.__doc__

    def get_sde(self) -> ContinuousDiscreteModel:
        F, L, H, Q, Pinf = self.get_rbf_sde(self.variance, self.lengthscales)
        return ContinuousDiscreteModel(Pinf, F, L, H, Q)

    def get_rbf_sde(self,
                    variance,
                    lengthscales,
                    order = 6) -> Tuple[tf.Tensor, ...]:
        """Get RBF SDE coefficients

        Parameters
        ----------
        variance : tf.Tensor
            The magnitude
        lengthscales : tf.Tensor
            The length-scale
        order : int
            Order of Taylor expansion

        Returns
        -------
        F, L, H, Q : tf.Tensor
            SDE coefficients.
        """
        _F, _L, _H, _q = self._get_unscaled_rbf_sde(order)

        dtype = config.default_float()
        F = tf.convert_to_tensor(_F, dtype=dtype)
        L = tf.convert_to_tensor(_L, dtype=dtype)
        H = tf.convert_to_tensor(_H, dtype=dtype)
        q = tf.convert_to_tensor(_q, dtype=dtype)

        dim = F.shape[0]

        ell_vec = lengthscales ** tf.range(dim, 0, -1, dtype=dtype)
        # F[-1, :] = F[-1, :] / ell_vec
        # F = tf.concat([F[:-1, :], F[None, -1, :] / ell_vec], axis=0)
        print(F)
        update_indices = [[dim - 1, k] for k in range(dim)]
        tf.compat.v1.scatter_nd_update(F, update_indices, F[-1, :] / ell_vec)
        # H[0] = H[0] / (lengthscales ** dim)
        print(F)
        q = variance * lengthscales * q

        F, L, H, q = self._balance_ss(F, L, H, q)

        Pinf = self._solve_lyap_vec(F, L, q)

        return F, L, H, q, Pinf


    def _get_unscaled_rbf_sde(self,
                              order: int = 6) -> Tuple[np.ndarray, ...]:
        """Get un-scaled RBF SDE.
        Pre-computed before loading to tensorflow.

        Parameters
        ----------
        order : int, default=6
            Order of Taylor expansion

        Returns
        -------
        F, L, H, Q : np.ndarray
            SDE coefficients.

        See Also
        --------
        se_to_ss.m
        """
        B = np.sqrt(2 * np.pi)
        A = np.zeros((2 * order + 1, ))

        i = 0
        for k in range(order, -1, -1):
            A[i] = 0.5 ** k / np.math.factorial(k)
            i = i + 2

        q = B / np.polyval(A, 0)

        LB = np.real(B / ((1j) ** np.arange(B.size - 1, -1, -1)))
        LA = np.real(A / ((1j) ** np.arange(A.size - 1, -1, -1)))

        BR = np.roots(LB)
        AR = np.roots(LA)

        GB = 1
        GA = np.poly(AR[np.real(AR) < 0])

        GA = GA / GA[-1]

        GB = GB / GA[0]
        GA = GA / GA[0]

        F = np.zeros((GA.size - 1, GA.size - 1))
        F[-1, :] = -GA[:0:-1]
        F[:-1, 1:] = np.eye(GA.size - 2)

        L = np.zeros((GA.size - 1, 1))
        L[-1] = 1

        H = np.zeros((1, GA.size - 1))
        H[0] = GB

        return F, L, H, q

    def _solve_lyap_vec(self,
                        F: tf.Tensor,
                        L: tf.Tensor,
                        q: tf.Tensor) -> tf.Tensor:
        """Vectorized Lyapunov equation solver

        F P + P F' + L q L' = 0

        Parameters
        ----------
        F : tf.Tensor
            ...
        L : tf.Tensor
            ...
        q : tf.Tensor
            ...

        Returns
        -------
        Pinf : tf.Tensor
            P_inf
        """
        dtype = config.default_float()

        dim = F.shape[0]

        # Plan A
        F1 = tf.experimental.numpy.kron(tf.eye(dim, dtype=dtype), F)
        F2 = tf.experimental.numpy.kron(F, tf.eye(dim, dtype=dtype))

        # Plan B
        op1 = tf.linalg.LinearOperator(F)
        op2 = tf.linalg.LinearOperator(tf.eye(dim, dtype=dtype))
        F1 = tf.linalg.LinearOperatorKronecker([op2, op1]).to_dense()
        F2 = tf.linalg.LinearOperatorKronecker([op1, op2]).to_dense()

        Q = L @ tf.transpose(L) * q

        ch = tf.linalg.cholesky(F1 + F2)
        Pinf = tf.reshape(tf.linalg.cholesky_solve(-ch, tf.reshape(Q, (dim**2, 1))),
                          (dim, dim))

        Pinf = 0.5 * (Pinf + tf.transpose(Pinf))

        return Pinf


    def _balance_ss(self,
                    F: tf.Tensor,
                    L: tf.Tensor,
                    H: tf.Tensor,
                    q: tf.Tensor,
                    iter: int = 5) -> Tuple[tf.Tensor, ...]:
        """Balance state-space model to have better numerical stability

        Parameters
        ----------
        F : tf.Tensor
            Matrix
        L : tf.Tensor
            Matrix
        H : tf.Tensor
            Measurement matrix
        q : tf.Tensor
            Spectral dnesity
        iter : int
            Iteration of balancing

        Returns
        -------
        Tuple[tf.Tensor]
        """
        dtype = config.default_float()

        dim = F.shape[0]

        D = tf.eye(dim, dtype=dtype)

        for k in range(iter):
            for i in range(dim):
                tmp = F[:, i]
                tmp[i] = 0
                c = tf.norm(tmp)
                tmp2 = F[i, :]
                tmp2[i] = 0
                r = tf.norm(tmp2)
                f = np.sqrt(r / c)
                D[i, i] = f * D[i, i]
                F[:, i] = f * F[:, i]
                F[i, :] = F[i, :] / f
                L[i, :] = L[i, :] / f
                H[:, i] = f * H[:, i]

        tmp3 = tf.reduce_max(tf.abs(L))
        L = L / tmp3
        q = tmp3 ** 2 * q

        tmp4 = tf.reduce_max(tf.abs(H))
        H = H / tmp4
        q = tmp4 ** 2 * q

        return F, L, H, q