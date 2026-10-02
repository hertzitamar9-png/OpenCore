import unittest
import torch
import torch.nn.functional as F
from echo_weight_adapter import Projection, ResidentPool, ResidentMLP, stage_block, route_ids

class NativeTrainingView(unittest.TestCase):
    def test_shared_training_uses_current_weights_after_every_optimizer_step(self):
        values=dict(expert_a=torch.zeros(5,1,2),expert_b=torch.zeros(5,2,1),
            seed_scale=torch.ones(5),shared_a=torch.ones(1,1,2),
            shared_b=torch.ones(1,2,1),shared_coeff=torch.ones(5,1))
        projection=Projection(values)
        # Opting in must not alter the untrained function, or train private KV /
        # expert tensors. Cache hits must never retain stale autograd graphs.
        x=torch.tensor([[2.,3.]]);seed=torch.zeros_like(x);ids=torch.tensor([0])
        projection.prepare(0,5,'cpu')
        before=projection(x,seed,ids).detach()
        enable=getattr(projection,'enable_shared_training',None)
        self.assertTrue(callable(enable),'shared-factor training is not implemented')
        enable('cpu')
        torch.testing.assert_close(projection(x,seed,ids).detach(),before)
        optimizer=torch.optim.SGD([projection.shared_a,projection.shared_b],lr=10)
        outputs=[]
        for _ in range(2):
            optimizer.zero_grad();projection.prepare(0,5,'cpu')
            output=projection(x,seed,ids);outputs.append(output.detach().clone())
            output.sum().backward()
            self.assertGreater(projection.shared_a.grad.abs().sum().item(),0)
            self.assertGreater(projection.shared_b.grad.abs().sum().item(),0)
            optimizer.step()
        self.assertFalse(torch.equal(outputs[0],outputs[1]))
        self.assertFalse(projection.expert_a.requires_grad)
        self.assertFalse(projection.expert_b.requires_grad)

    def test_coverage_routes_are_bounded_and_follow_native_shards(self):
        for position in (0,100,2047,4096):
            offset,count=stage_block(position)
            ids=route_ids(torch.arange(16)+position,31,offset,count)
            self.assertTrue(torch.all((ids>=offset)&(ids<offset+count)))
            for lane in range(5): self.assertTrue(torch.all(ids[:,lane]%5==(offset+lane)%5))
        self.assertEqual(stage_block(0),(0,250))
        self.assertEqual(stage_block(999999),(9750,250))

    def test_shared_experts_actually_change_next_layer_input_and_receive_gradients(self):
        torch.manual_seed(4)
        def values(input_size,output_size):
            return dict(expert_a=torch.randn(250,3,input_size)*.01,expert_b=torch.randn(250,output_size,3)*.01,
                seed_scale=torch.ones(250),shared_a=torch.randn(2,2,input_size)*.01,
                shared_b=torch.randn(2,output_size,2)*.01,shared_coeff=torch.ones(250,2))
        values_by_prefix={f'{prefix}.{key}':value for prefix,i,o in [('gate',4,10),('up',4,10),('down',10,4)]
                          for key,value in values(i,o).items()}
        pool=ResidentPool(values_by_prefix)
        class Seed(torch.nn.Module):
            def __init__(self):
                super().__init__();self.gate_proj=torch.nn.Linear(4,10,bias=False)
                self.up_proj=torch.nn.Linear(4,10,bias=False);self.down_proj=torch.nn.Linear(10,4,bias=False)
        seed=Seed()
        for p in seed.parameters(): p.requires_grad=False
        wrapper=ResidentMLP(seed,pool,0);x=torch.randn(1,2,4)
        before=wrapper(x)
        before.square().mean().backward()
        self.assertGreater(pool.down.seed_scale.grad.abs().sum().item(),0)
        self.assertGreater(pool.down.shared_coeff.grad.abs().sum().item(),0)
        with torch.no_grad(): pool.down.seed_scale.mul_(.5)
        self.assertFalse(torch.allclose(before,wrapper(x)))
        self.assertTrue(all(p.grad is None for p in seed.parameters()))

    def test_zero_residual_sharded_pool_matches_dense_seed_ffn(self):
        torch.manual_seed(7)
        class Seed(torch.nn.Module):
            def __init__(self):
                super().__init__();self.gate_proj=torch.nn.Linear(4,10,bias=False)
                self.up_proj=torch.nn.Linear(4,10,bias=False);self.down_proj=torch.nn.Linear(10,4,bias=False)
        seed=Seed();data={}
        for prefix,i,o in [('gate',4,10),('up',4,10),('down',10,4)]:
            data.update({prefix+'.'+key:value for key,value in dict(expert_a=torch.zeros(250,3,i),expert_b=torch.zeros(250,o,3),
                seed_scale=torch.ones(250),shared_a=torch.zeros(2,2,i),shared_b=torch.zeros(2,o,2),shared_coeff=torch.ones(250,2)).items()})
        x=torch.randn(1,5,4)
        expected=seed.down_proj(seed.up_proj(x)*F.silu(seed.gate_proj(x)))
        actual=ResidentMLP(seed,ResidentPool(data),3)(x)
        torch.testing.assert_close(actual,expected,rtol=1e-5,atol=1e-6)

if __name__=='__main__': unittest.main()
