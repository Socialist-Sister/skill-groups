import unittest

from sg import errors


class TestErrors(unittest.TestCase):
    def test_sg_error_inherits_exception(self):
        self.assertTrue(issubclass(errors.SgError, Exception))

    def test_user_error_inherits_sg_error(self):
        self.assertTrue(issubclass(errors.UserError, errors.SgError))

    def test_env_error_inherits_sg_error(self):
        self.assertTrue(issubclass(errors.EnvError, errors.SgError))

    def test_sg_error_code_is_1(self):
        self.assertEqual(errors.SgError.code, 1)

    def test_user_error_code_is_1(self):
        self.assertEqual(errors.UserError.code, 1)

    def test_env_error_code_is_2(self):
        self.assertEqual(errors.EnvError.code, 2)


if __name__ == "__main__":
    unittest.main()
