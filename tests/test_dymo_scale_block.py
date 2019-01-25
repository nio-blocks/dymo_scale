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

    reset = Mock()
    is_kernel_driver_active = Mock()
    detach_kernel_driver = Mock()
    set_configuration = Mock()
    read = Mock(return_value=dummy_packet)

    def __init__(self):
        super().__init__()
        self.endpoint = DummyEndpoint()

    def __getitem__(self, index):
        return {(0, 0): [self.endpoint]}

class DummyEndpoint():

    bEndpointAddress = 0x82
    wMaxPacketSize = 0x08


@not_discoverable
class ReadEvent(DymoScale):

    def __init__(self, event):
        super().__init__()
        self._event = event

    def notify_signals(self, signals):
        super().notify_signals(signals)
        self._kill = True  # set here to stop iteration during test
        self._event.set()

class TestDymoScale(NIOBlockTestCase):

    @patch('usb.core')
    def test_read_from_scale(self, mock_usb_core):
        """Discover, connect to, and read from a scale."""
        mock_device = DummyDevice()
        mock_device.is_kernel_driver_active.return_value = True
        mock_usb_core.find.return_value = mock_device
        e = Event()
        blk = ReadEvent(e)
        cfg = {}
        self.configure_block(blk, cfg)
        blk.start()
        e.wait(1)  # wait up to 1 second for signals from block
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
