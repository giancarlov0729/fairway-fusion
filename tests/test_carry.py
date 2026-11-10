import math, unittest
from fairway_fusion.analysis.carry import estimate_carry, carry_metrics, Assumptions, G, M_TO_YD

def A(**k): return Assumptions(**k)

class TestCarry(unittest.TestCase):
    def test_vacuum_45_deg_matches_formula(self):
        r = estimate_carry(50, 45, A(model="vacuum"), "mps")
        self.assertAlmostEqual(r.carry_m, 50**2 / G, places=6)

    def test_vacuum_symmetry_30_60(self):
        a = estimate_carry(40, 30, A(model="vacuum"), "mps").carry_m
        b = estimate_carry(40, 60, A(model="vacuum"), "mps").carry_m
        self.assertAlmostEqual(a, b, places=6)

    def test_vacuum_apex_and_time(self):
        r = estimate_carry(30, 90 - 1e-6, A(model="vacuum"), "mps")
        self.assertAlmostEqual(r.apex_height_m, 30**2 / (2 * G), places=3)
        self.assertAlmostEqual(r.flight_time_s, 2 * 30 / G, places=3)

    def test_drag_zero_cd_matches_vacuum(self):
        r = estimate_carry(50, 30, A(model="drag", drag_coeff=0.0), "mps")
        self.assertAlmostEqual(r.carry_m / (50**2 * math.sin(math.radians(60)) / G), 1, places=3)

    def test_ordering_vacuum_drag_spin(self):
        v = estimate_carry(67, 11, A(model="vacuum"), "mps").carry_yd
        d = estimate_carry(67, 11, A(model="drag"), "mps").carry_yd
        s = estimate_carry(67, 11, A(model="drag_spin"), "mps").carry_yd
        self.assertLess(d, v)
        self.assertGreater(s, d)

    def test_monotonic_in_speed(self):
        c = [estimate_carry(s, 12, speed_unit="mph").carry_m for s in (80, 100, 120, 140, 160)]
        self.assertEqual(c, sorted(c))

    def test_unit_conversion(self):
        self.assertAlmostEqual(estimate_carry(100, 15, speed_unit="mph").carry_m,
                               estimate_carry(44.704, 15, speed_unit="mps").carry_m, places=6)

    def test_yards_metres_consistent(self):
        r = estimate_carry(55, 14)
        self.assertAlmostEqual(r.carry_yd, r.carry_m * M_TO_YD, places=9)

    def test_step_size_converged(self):
        a = estimate_carry(67, 11, A(time_step_s=0.004)).carry_m
        b = estimate_carry(67, 11, A(time_step_s=0.0005)).carry_m
        self.assertAlmostEqual(a / b, 1, places=3)

    def test_realistic_ranges(self):
        d = estimate_carry(150, 11, A(backspin_rpm=2600), "mph").carry_yd
        i = estimate_carry(120, 17, A(backspin_rpm=6500), "mph").carry_yd
        self.assertTrue(200 < d < 260); self.assertTrue(150 < i < 200)

    def test_invalid_values(self):
        for s, a in [(0, 10), (-5, 10), (1e9, 10), (100, 0), (100, 90), (100, -3),
                     (100, 120), (float("nan"), 10), (100, float("inf"))]:
            with self.subTest(s=s, a=a), self.assertRaises(ValueError):
                estimate_carry(s, a)

    def test_invalid_types(self):
        for s, a in [("100", 10), (100, None), (True, 10)]:
            with self.subTest(s=s, a=a), self.assertRaises(TypeError):
                estimate_carry(s, a)

    def test_default_unit_is_mps(self):
        self.assertAlmostEqual(estimate_carry(50, 15).carry_m,
                               estimate_carry(50, 15, speed_unit="mps").carry_m)

    def test_carry_metrics_from_launch_metrics(self):
        m = {"speed_m_s": 50.0, "launch_angle_deg": 15.0, "launch_direction_deg": 0.0}
        out = carry_metrics(m)
        self.assertIsNone(out["carry_error"])
        self.assertAlmostEqual(out["estimated_carry_m"], estimate_carry(50, 15).carry_m)
        self.assertTrue(out["carry_is_estimate"])
        self.assertEqual(out["carry_assumptions"]["model"], "drag_spin")

    def test_carry_metrics_never_raises(self):
        for m in [{"speed_m_s": 0.0, "launch_angle_deg": None},
                  {"speed_m_s": 50.0, "launch_angle_deg": -5.0},
                  {"speed_m_s": 50.0}, {}]:
            out = carry_metrics(m)
            self.assertIsNone(out["estimated_carry_m"]); self.assertIsInstance(out["carry_error"], str)

    def test_bad_model_and_unit(self):
        with self.assertRaises(ValueError): estimate_carry(100, 10, A(model="x"))
        with self.assertRaises(ValueError): estimate_carry(100, 10, speed_unit="kph")

if __name__ == "__main__":
    unittest.main()
