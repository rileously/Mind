import ctypes
import os
import unittest


@unittest.skipUnless(os.name == "nt", "Windows INPUT layout is required")
class SelectionStructTests(unittest.TestCase):
    def test_input_structure_matches_windows_x64_layout(self):
        from mind.selection import INPUT

        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected)


if __name__ == "__main__":
    unittest.main()
