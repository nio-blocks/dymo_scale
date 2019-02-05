from array import array
from threading import Event
from unittest.mock import Mock, patch
from nio.block.terminals import DEFAULT_TERMINAL
from nio.signal.base import Signal
from nio.testing.block_test_case import NIOBlockTestCase
from nio.util.discovery import not_discoverable
from ..dymo_scale_block import DymoScale


class DummyDevice():

    dummy_packet = array('B', [3, 4, 11, 255, 100, 1])  # 35.6 oz

    def __init__(self):
        super().__init__()
        self.endpoint = DummyEndpoint()
        self.reset = Mock()
        self.is_kernel_driver_active = Mock()
        self.attach_kernel_driver = Mock()
        self.detach_kernel_driver = Mock()
        self.set_configuration = Mock()
        self.read = Mock(return_value=self.dummy_packet)

    def __getitem__(self, index):
        return {(0, 0): [self.endpoint]}

class DummyEndpoint():

    bEndpointAddress = 0x82
    wMaxPacketSize = 0x08

@not_discoverable
class ScaleEvents(DymoScale):
    """Block extension for testing which sets (optional) Events.

    Args:
        notify_event: Event to be set when signals are notified
        ok_event: Event to be set when 'ok' status is set
        warning_event: Event to be set when 'warning' status is set

    """

    def __init__(self, notify_event=None, ok_event=None, warning_event=None):
        super().__init__()
        self.notify_event = notify_event
        self.ok_event = ok_event
        self.warning_event = warning_event

    def notify_signals(self, signals):
        super().notify_signals(signals)
        if self.notify_event:
            self.notify_event.set()

    def set_status(self, status,  message=''):
        super().set_status(status)
        if status == 'ok' and self.ok_event:
            self.ok_event.set()
        if status == 'warning' and self.warning_event:
            self.warning_event.set()

class TestDymoScale(NIOBlockTestCase):

    @patch('usb.core')
    def test_read_from_scale(self, mock_usb_core):
        """Discover, connect to, and read from a scale."""
        mock_device = DummyDevice()
        mock_device.is_kernel_driver_active.return_value = True
        mock_usb_core.find.return_value = mock_device
        notify_event = Event()
        blk = ScaleEvents(notify_event)
        cfg = {}
        self.configure_block(blk, cfg)
        blk.start()
        notify_event.wait(1)  # wait up to 1 second for signals from block
        blk.stop()
        mock_usb_core.find.assert_called_once_with(
            idVendor=blk.manufacturer_id,
            idProduct=blk.product_id)
        mock_device.reset.assert_called_once_with()
        mock_device.is_kernel_driver_active.assert_called_once_with(0)
        mock_device.detach_kernel_driver.assert_called_once_with(0)
        mock_device.set_configuration.assert_called_once_with()
        mock_device.read.assert_called_once_with(
            DummyEndpoint.bEndpointAddress, DummyEndpoint.wMaxPacketSize)
        self.assert_num_signals_notified(1)
        self.assertDictEqual(
            self.last_notified[DEFAULT_TERMINAL][0].to_dict(),
            {'units': 'oz', 'weight': 35.6})

    @patch(DymoScale.__module__ + '.sleep')
    @patch('usb.core')
    def test_warning_status_connect(self, mock_usb_core, mock_sleep):
        """The block is in warning status while the hardware is unavailable."""
        mock_device = DummyDevice()
        mock_usb_core.find.side_effect = [None, mock_device]
        ok_event = Event()
        warning_event = Event()
        blk = ScaleEvents(ok_event=ok_event, warning_event=warning_event)
        self.configure_block(blk, {})
        blk.start()

        # Hardware is not found, block is in warning
        self.assertTrue(warning_event.wait(1))
        # Second attempt is successful, block is ok
        self.assertTrue(ok_event.wait(1))
        blk.stop()

    @patch(DymoScale.__module__ + '.sleep')
    @patch('usb.core')
    def test_warning_status_connect_exception(self, mock_usb_core, mock_sleep):
        """Something unexpected as happened in _connect."""
        mock_device = DummyDevice()
        mock_device.set_configuration.side_effect = Exception
        mock_usb_core.find.return_value = mock_device
        warning_event = Event()
        blk = ScaleEvents(warning_event=warning_event)
        self.configure_block(blk, {})
        blk.start()
        # Hardware is not found, block is in warning
        self.assertTrue(warning_event.wait(1))
        blk.stop()

    @patch(DymoScale.__module__ + '.sleep')
    @patch('usb.core')
    def test_warning_status_read(self, mock_usb_core, mock_sleep):
        """The block is in warning status while the hardware is unavailable."""
        mock_device = DummyDevice()
        mock_usb_core.find.return_value = mock_device
        ok_event = Event()
        notify_event = Event()
        warning_event = Event()
        blk = ScaleEvents(
            ok_event=ok_event,
            notify_event=notify_event,
            warning_event=warning_event)
        self.configure_block(blk, {})
        blk.start()

        # first attempt to read works and a signal notified
        self.assertTrue(notify_event.wait(1))
        self.assertDictEqual(
            self.last_notified[DEFAULT_TERMINAL][-1].to_dict(),
            {'weight': 35.6, 'units': 'oz'})
        notify_event.clear()  # we're going to use this one again

        # something goes wrong in read(), the block is back in warning
        mock_device.read.side_effect = Exception
        self.assertTrue(warning_event.wait(1))

        # block reconnects on its own, and returns to ok
        mock_device.read = Mock(return_value=DummyDevice.dummy_packet)
        self.assertTrue(ok_event.wait(1))
        # read operations resume and we get another signal
        self.assertTrue(notify_event.wait(1))
        blk.stop()

    @patch('usb.core')
    @patch('{}.sleep'.format(DymoScale.__module__))
    def test_reconnect_interval(self, mock_sleep, mock_usb_core):
        """Wait for the configured interval between connection attempts."""
        test_interval = 3.14
        mock_device = DummyDevice()
        mock_usb_core.find.side_effect = [None, mock_device]
        ok_event = Event()
        blk = ScaleEvents(ok_event=ok_event)
        cfg = {
            'reconnect_interval': test_interval,
        }
        self.configure_block(blk, cfg)
        blk.start()
        # failed to connect, check retry interval
        self.assertTrue(ok_event.wait(1))
        self.assertEqual(mock_sleep.call_count, 1)
        self.assertEqual(
            mock_sleep.call_args_list[0][0], (test_interval,))

    @patch('usb.core')
    @patch('{}.sleep'.format(DymoScale.__module__))
    def test_read_interval(self, mock_sleep, mock_usb_core):
        """Wait for the configured interval between read operations."""
        test_interval = 3.14
        mock_device = DummyDevice()
        mock_usb_core.find.return_value = mock_device
        notify_event = Event()
        blk = ScaleEvents(notify_event=notify_event)
        cfg = {
            'read_interval': test_interval,
        }
        self.configure_block(blk, cfg)
        blk.start()
        self.assertTrue(notify_event.wait(1))
        self.assertEqual(
            mock_sleep.call_args_list[-1][0], (test_interval,))
