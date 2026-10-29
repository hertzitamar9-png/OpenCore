import unittest
from comfy_worker import image_memory_plan

class ImageMemoryTests(unittest.TestCase):
    def test_gpu_residency_and_cpu_staging_are_budgeted_separately(self):
        plan=image_memory_plan([7_000_000_000,6_500_000_000,300_000_000],18_000_000_000,12_000_000_000)
        self.assertTrue(plan['fits'])
        self.assertGreaterEqual(plan['hostRequiredBytes'],9_000_000_000)
        self.assertLess(plan['hostRequiredBytes'],18_000_000_000)
        self.assertGreater(plan['gpuWeightBudgetBytes'],0)
        self.assertLess(plan['residentHostBytes'],plan['cpuStagingBytes'])
        self.assertFalse(image_memory_plan([7_000_000_000,6_500_000_000],10_000_000_000,24_000_000_000)['fits'])

    def test_gpu_memory_cannot_replace_the_largest_host_load_buffer(self):
        plan=image_memory_plan([22_000_000_000,2_000_000_000],18_000_000_000,24_000_000_000)
        self.assertFalse(plan['fits'])
        self.assertGreaterEqual(plan['hostRequiredBytes'],24_000_000_000)

    def test_unavailable_gpu_requires_the_host_to_hold_the_complete_package(self):
        plan=image_memory_plan([7_000_000_000,6_500_000_000],12_000_000_000,0)
        self.assertFalse(plan['fits'])
        self.assertEqual(plan['gpuWeightBudgetBytes'],0)

if __name__=='__main__':unittest.main()
