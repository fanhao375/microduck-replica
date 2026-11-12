"""Boot arguments must release the HAT UART without changing the boot device."""

import unittest

from prepare_image import without_uart_console


class BootArguments(unittest.TestCase):
    def test_removes_both_console_names_and_early_output(self):
        original = ('append root=UUID=unchanged console=ttyAS0,115200n8 '
                    'earlyprintk=sunxi-uart,0x2500000 rootwait earlycon '
                    'console=tty1 rw console=ttyS2,115200\n')
        self.assertEqual(without_uart_console(original).split(),
                         ['append', 'root=UUID=unchanged', 'rootwait', 'console=tty1', 'rw'])

    def test_preserves_rescue_and_unrelated_console(self):
        original = ('label rescue\n append root=UUID=x console=ttyAS1,115200 '
                    'earlycon=uart8250,mmio32,0x2500000 rw single\n')
        self.assertEqual(without_uart_console(original).split(),
                         ['label', 'rescue', 'append', 'root=UUID=x',
                          'console=ttyAS1,115200', 'rw', 'single'])

    def test_idempotent_and_does_not_match_argument_values(self):
        text = 'root=UUID=x quiet consoleblank=0 custom=earlycon console=tty1\n'
        self.assertEqual(without_uart_console(text), text)
        self.assertEqual(without_uart_console(without_uart_console(text)), text)


if __name__ == '__main__':
    unittest.main()
