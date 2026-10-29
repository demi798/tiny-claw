import unittest
import mathutil

class TestMultiply(unittest.TestCase):
    def test_positive_numbers(self):
        """测试两个正数相乘"""
        self.assertEqual(mathutil.multiply(3, 4), 12)
        self.assertEqual(mathutil.multiply(5, 6), 30)
        self.assertEqual(mathutil.multiply(10, 10), 100)

    def test_negative_numbers(self):
        """测试负数相乘"""
        self.assertEqual(mathutil.multiply(-2, 3), -6)
        self.assertEqual(mathutil.multiply(2, -3), -6)
        self.assertEqual(mathutil.multiply(-2, -3), 6)

    def test_zero(self):
        """测试零相乘"""
        self.assertEqual(mathutil.multiply(0, 5), 0)
        self.assertEqual(mathutil.multiply(5, 0), 0)
        self.assertEqual(mathutil.multiply(0, 0), 0)

    def test_large_numbers(self):
        """测试大数相乘"""
        self.assertEqual(mathutil.multiply(1000, 1000), 1000000)
        self.assertEqual(mathutil.multiply(12345, 67890), 838102050)

    def test_one(self):
        """测试乘以1"""
        self.assertEqual(mathutil.multiply(7, 1), 7)
        self.assertEqual(mathutil.multiply(1, 7), 7)

if __name__ == '__main__':
    unittest.main()