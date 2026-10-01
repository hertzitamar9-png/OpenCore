import unittest
import torch
from echo_rl import group_advantages, policy_loss, response_logprobs, prompt_ids, qualification_status
from echo_weight_adapter import stage_segments, stage_block


class RewardLearning(unittest.TestCase):
    def test_correctness_reward_pushes_success_up_and_failure_down(self):
        advantages = group_advantages([1., 0., .5, .5])
        logits = torch.tensor([-2., -2., -2., -2.], requires_grad=True)
        loss = sum(policy_loss(logits[i:i+1], logits[i:i+1].detach(), advantages[i]) for i in range(4))
        loss.backward()
        self.assertLess(logits.grad[0].item(), 0)
        self.assertGreater(logits.grad[1].item(), 0)
        self.assertEqual(logits.grad[2].item(), 0)

    def test_constant_rewards_cannot_manufacture_a_learning_signal(self):
        self.assertEqual(group_advantages([1., 1., 1., 1.]).tolist(), [0.] * 4)
        for rewards in ([float('nan'), 1.], [-1., 0.], [2., 1.]):
            with self.assertRaises(ValueError): group_advantages(rewards)

    def test_clipping_stops_increasing_probability_past_the_trust_region(self):
        new = torch.tensor([-.5], requires_grad=True)
        loss = policy_loss(new, torch.tensor([-2.]), 1., beta=0.)
        loss.backward()
        self.assertEqual(new.grad.item(), 0.)

    def test_predicts_response_tokens_from_preceding_positions_only(self):
        class Pool: position_start = 99; decode_start = None
        class Model:
            opencore_expert_pool = Pool()
            def __call__(self, input_ids, **kwargs):
                self.positions = kwargs['logits_to_keep'].tolist()
                self.ids = input_ids.tolist()
                return type('Output', (), {'logits': torch.zeros(1, 2, 10)})()
        model = Model()
        probs = response_logprobs(model, [1, 2, 3], [4, 5], 'cpu')
        self.assertEqual(model.positions, [2, 3])
        self.assertEqual(model.ids, [[1, 2, 3, 4]])
        self.assertEqual(probs.shape, (2,))
        self.assertEqual(model.opencore_expert_pool.decode_start, 3)

    def test_long_prompt_is_rejected_without_losing_requirements(self):
        class Tokenizer:
            def apply_chat_template(self, *args, **kwargs): return list(range(193))
        with self.assertRaises(ValueError): prompt_ids(Tokenizer(), 'task', 192)

    def test_training_routes_match_prompt_prefill_then_tokenwise_decoding(self):
        segments = stage_segments(0, 180, decode_start=80)
        observed = {}
        for start, end, offset, count in segments:
            observed.update({p: (offset, count) for p in range(start, end)})
        for p in range(180):
            self.assertEqual(observed[p], stage_block(0 if p < 80 else p))
        self.assertEqual(stage_segments(80, 1, None), [(0, 1, *stage_block(80))])

    def test_completed_training_keeps_a_failed_native_speed_gate(self):
        baseline = {'passed': 1, 'mean_case_reward': .5}
        candidate = {'passed': 2, 'mean_case_reward': .75, 'native_tokens_per_second': 19.9}
        self.assertEqual(qualification_status(baseline, candidate), 'rejected-native-speed')
        candidate['native_tokens_per_second'] = 20.
        self.assertEqual(qualification_status(baseline, candidate), 'complete')

    def test_speed_cannot_hide_native_quality_regression(self):
        baseline = {'passed': 2, 'mean_case_reward': .5}
        candidate = {'passed': 1, 'mean_case_reward': .75, 'native_tokens_per_second': 26.}
        self.assertEqual(qualification_status(baseline, candidate), 'rejected-native-heldout-regression')
        candidate.update(passed=2, mean_case_reward=.4)
        self.assertEqual(qualification_status(baseline, candidate), 'rejected-native-heldout-regression')

    def test_missing_or_invalid_speed_is_never_qualified(self):
        baseline = {'passed': 1, 'mean_case_reward': .5}
        for speed in (None, float('nan'), float('inf'), -1.):
            candidate = dict(baseline, native_tokens_per_second=speed)
            self.assertEqual(qualification_status(baseline, candidate), 'rejected-native-speed')


if __name__ == '__main__': unittest.main()
