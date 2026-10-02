import unittest
import torch
from echo_shared_training import complete_loss, candidate_status


class CompleteExpertTraining(unittest.TestCase):
    def test_complete_loss_predicts_all_answer_tokens_with_native_decode_routes(self):
        class Pool: position_start=9;decode_start=None
        class Model:
            opencore_expert_pool=Pool()
            def __call__(self,input_ids,**kwargs):
                self.tokens=input_ids.tolist();self.positions=kwargs['logits_to_keep'].tolist()
                return type('Output',(),{'logits':torch.zeros(1,3,10,requires_grad=True)})()
        model=Model();loss=complete_loss(model,{'prompt_ids':[1,2],'response_ids':[3,4,9]},'cpu')
        self.assertEqual(model.tokens,[[1,2,3,4]])
        self.assertEqual(model.positions,[1,2,3])
        self.assertEqual(model.opencore_expert_pool.decode_start,2)
        self.assertTrue(loss.requires_grad)
        self.assertAlmostEqual(float(loss.detach()),2.302585,places=5)

    def test_lower_loss_cannot_qualify_unchanged_or_slower_coding(self):
        baseline={'passed':10,'mean_case_reward':.6}
        candidate={'passed':10,'mean_case_reward':.7,'speed':25.}
        self.assertEqual(candidate_status(baseline,candidate,.8,.7),'no-verified-coding-gain')
        candidate['passed']=11
        self.assertEqual(candidate_status(baseline,candidate,.8,.7),'qualified-local-coding-gain')
        candidate['speed']=19.
        self.assertEqual(candidate_status(baseline,candidate,.8,.7),'rejected-native-speed')
        candidate.update(speed=25.,mean_case_reward=.5)
        self.assertEqual(candidate_status(baseline,candidate,.8,.7),'rejected-coding-regression')
        candidate['mean_case_reward']=.7
        self.assertEqual(candidate_status(baseline,candidate,.8,.83),'rejected-heldout-loss')


if __name__=='__main__': unittest.main()
