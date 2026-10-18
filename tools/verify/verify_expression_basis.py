"""Regressions for coherent emotional-mouth and speech composition."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/lib'))
import moe_expression_basis as E


class SharedCompositionTests(unittest.TestCase):
    def setUp(self):
        self.matrix=np.array([[1.,0.,0.,1.,0.,0.],[0.,1.,0.,0.,-1.,0.]])
        self.basis={'version':1,'keys':['open','smile'],'bones':['a','b'],
                    'neutral_t':[[1.,2.,3.],[4.,5.,6.]],'matrix':self.matrix.tolist()}
        self.clip={'bones':[{'name':n,'t':self.basis['neutral_t'][i]} for i,n in enumerate(('a','b'))]}
    def encode(self,keys):
        arr=(np.asarray(keys)@self.matrix).reshape(-1,2,3)+np.asarray(self.basis['neutral_t'])
        return {n:arr[:,i].tolist() for i,n in enumerate(('a','b'))}
    def test_preserves_emotion_while_speech_opens_shared_mouth(self):
        emotion=np.array([[.4,.8],[.4,.8],[.4,.8]])
        speech=np.array([[.1,0.],[.9,.1],[.2,0.]])
        target=self.encode(emotion)
        E.overlay(target,self.clip,self.encode(speech),self.clip,self.basis)
        expected=self.encode(np.maximum(emotion,speech))
        for name in target:np.testing.assert_allclose(target[name],expected[name],atol=1e-12)
    def test_gain_caps_coefficients_instead_of_summing_same_open_slot(self):
        target=self.encode([[.5,.4]])
        E.overlay(target,self.clip,self.encode([[.8,.1]]),self.clip,self.basis,.5)
        for name,value in self.encode([[.5,.4]]).items():np.testing.assert_allclose(target[name],value,atol=1e-12)
    def test_static_closed_loop_keeps_endpoints_and_discrete_velocity(self):
        emotion=np.tile([.3,.2],(6,1));speech=np.array([[0.,0.],[0.,0.],[.7,0.],[.4,0.],[0.,0.],[0.,0.]])
        target=self.encode(emotion)
        E.overlay(target,self.clip,self.encode(speech),self.clip,self.basis)
        for seq in target.values():
            np.testing.assert_allclose(seq[0],seq[-1],atol=1e-12)
            np.testing.assert_allclose(np.subtract(seq[1],seq[0]),np.subtract(seq[-1],seq[-2]),atol=1e-12)
    def test_incompatible_basis_is_rejected(self):
        target=self.encode([[.3,.2]])
        target['a'][0][2]+=.01
        with self.assertRaisesRegex(ValueError,'do not match'):
            E.overlay(target,self.clip,self.encode([[.5,.1]]),self.clip,self.basis)

    def test_material_negative_native_key_is_rejected(self):
        target=self.encode([[.3,-.01]])
        with self.assertRaisesRegex(ValueError,'do not match'):
            E.overlay(target,self.clip,self.encode([[.5,.1]]),self.clip,self.basis)

    def test_boundary_noise_has_nonnegative_solution_within_same_source_bound(self):
        matrix=np.array([[1.,0.,0.],[1.,.05,0.]])
        values=np.array([[.2,-1.25e-5,0.]])
        keys=values@np.linalg.pinv(matrix,rcond=1e-9)
        self.assertLess(keys.min(),-2e-4)
        corrected=E._nonnegative_source_keys(values,matrix,keys)
        self.assertGreaterEqual(corrected.min(),0.)
        self.assertLess(np.max(np.abs(corrected@matrix-values)),2e-4)
        np.testing.assert_array_equal(values,[[.2,-1.25e-5,0.]])

    def test_accepted_rows_keep_exact_prior_coefficients(self):
        matrix=np.array([[1.,0.,0.],[1.,.05,0.]])
        values=np.array([[.2,.01,0.],[.2,-5e-6,0.]])
        keys=values@np.linalg.pinv(matrix,rcond=1e-9)
        self.assertGreaterEqual(keys.min(),-2e-4)
        self.assertIs(E._nonnegative_source_keys(values,matrix,keys),keys)

    def test_emotion_only_composition_recovers_zero_key_boundary(self):
        basis={'keys':['open','corner'],'bones':['mouth'],'neutral_t':[[0.,0.,0.]],
               'matrix':[[1.,0.,0.],[1.,.01,0.]]}
        clip={'bones':[{'name':'mouth','t':[0.,0.,0.]}]}
        target={'mouth':[(.2,-3e-6,0.),(.2,-3e-6,0.)], 'neck':[(1.,2.,3.)]*2}
        original=np.asarray(target['mouth'])
        E.project_emotion(target,clip,basis)
        result=np.asarray(target['mouth']);matrix=np.asarray(basis['matrix'])
        self.assertGreaterEqual(float((result@np.linalg.pinv(matrix)).min()),-1e-12)
        self.assertLess(float(np.abs(result-original).max()),2e-4)
        self.assertEqual(target['neck'],[(1.,2.,3.)]*2)
        np.testing.assert_array_equal(result[0],result[-1])

    def test_emotion_only_preserves_valid_native_expression(self):
        target=self.encode([[.3,.2],[.7,.4],[.3,.2]])
        expected=self.encode([[.3,.2],[.7,.4],[.3,.2]])
        E.project_emotion(target,self.clip,self.basis)
        for name in expected:np.testing.assert_allclose(target[name],expected[name],atol=1e-12)

    def test_emotion_only_rejects_material_negative_native_shape(self):
        with self.assertRaisesRegex(ValueError,'does not match'):
            E.project_emotion(self.encode([[.3,-.01]]),self.clip,self.basis)

    def test_emotion_only_handles_large_canceling_basis_without_clamp_drift(self):
        basis={'keys':['open','corner'],'bones':['mouth'],'neutral_t':[[0.,0.,0.]],
               'matrix':[[600.,0.,0.],[600.,1.,0.]]}
        clip={'bones':[{'name':'mouth','t':[0.,0.,0.]}]}
        target={'mouth':[(300.,-1e-5,0.)]}
        E.project_emotion(target,clip,basis)
        np.testing.assert_allclose(target['mouth'],[(300.,0.,0.)],atol=1e-12)


class SourceBasisRecoveryTests(unittest.TestCase):
    def test_existing_valid_least_squares_result_is_unchanged(self):
        coefficients=np.array([[0.,0.],[1.,0.],[0.,1.],[.4,.8]])
        values=coefficients@np.array([[1.,2.,0.],[0.,1.,2.]])
        expected=np.linalg.lstsq(coefficients,values,rcond=1e-9)[0]
        actual,error,refinement=E._recover_basis(coefficients,values)
        np.testing.assert_array_equal(actual,expected)
        self.assertIsNone(refinement)
        self.assertLess(error,1e-4)

    def test_recoverable_quantized_outlier_stays_within_unchanged_limit(self):
        coefficients=np.ones((101,1));values=np.full((101,1),-600.)
        values[-1,0]+=1.5e-4
        before=values.copy()
        basis,error,refinement=E._recover_basis(coefficients,values)
        self.assertGreater(refinement['least_squares_error_max'],1e-4)
        self.assertLess(error,1e-4)
        self.assertEqual(refinement['error_limit'],1e-4)
        np.testing.assert_array_equal(values,before)
        self.assertLess(np.max(np.abs(coefficients@basis-values)),1e-4)

    def test_inconsistent_source_samples_remain_rejected(self):
        with self.assertRaisesRegex(ValueError,'not recoverable'):
            E._recover_basis(np.ones((2,1)),np.array([[0.],[.01]]))


if __name__=='__main__':unittest.main()
