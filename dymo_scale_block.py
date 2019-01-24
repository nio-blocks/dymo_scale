from threading import current_thread
from time import sleep
import usb.core
from nio import GeneratorBlock, Signal
from nio.properties import VersionProperty
from nio.util.threading import spawn


class DymoScale(GeneratorBlock):

    version = VersionProperty('0.1.0')

    read_interval = 0.5  # seconds between reads
    reconnect_interval = 10

    manufacturer_id = 0x0922
    product_id = 0x8003

    def __init__(self):
        super().__init__()
        self.device = None
        self._reader_thread = None
        self._kill = False

        self._address = None
        self._packet_size = None

    def start(self):
        super().start()
        spawn(self._connect)

    def stop(self):
        self._disconnect()
        super().stop()

    def _connect(self):
        self.logger.debug('Connecting to scale device...')
        while not self.device:
            try:
                self.device = usb.core.find(
                    self.manufacturer_id, self.product_id)
                self.logger.debug('Device discovered')
                self.device.reset()
                self.logger.debug('Device reset')
                interface = 0
                if self.device.is_kernel_driver_attached(interface):
                    self.device.detach_kernel_driver(interface)
                    self.logger.debug('Detached kernel driver')
                self.device.set_configuration()
                self.logger.debug('Device Configured')
                self.device.connect()
                self.logger.debug('Device Connected!')
                endpoint = self.device[interface][(0, 0)][0]
                self._address = endpoint.bEndpointAddress
                self._packet_size = endpoint.wMaxPacketSize
            except:
                msg = 'Unable to connect to scale, trying again in {} seconds'
                self.logger.error(msg.format(self.reconnect_interval))
                sleep(self.reconnect_interval)
        self._kill = False
        self._reader_thread = spawn(self._reader)

    def _disconnect(self):
        self.logger.debug('Halting read operations')
        self._kill = True
        self.device = None

    def _reader(self):
        thread_id = current_thread().name
        self.logger.debug('Reader thread {} spawned'.format(thread_id))
        while not self._kill:
            try:
                data = self.device.read(self._address, self._packet_size)
            except:
                self.logger.exception('Read operation from scale failed')
                self._disconnect()
                self._connect()
                break
            units, weight = self._parse_weight(data)
            signal_dict = {
                'units': units,
                'weight': weight,
            }
            self.notify_signals([Signal(signal_dict)])
            sleep(self.read_interval)
        self.logger.debug('Reader thread {} completed'.format(thread_id))

    def _parse_weight(self, data):
        # battery = data[0]
        state = data[1]
        if state == 5:  # scale value is negative
            sign = -1
        else:
            sign = 1
        units = data[2]
        if units == 2:
            units = 'g'
        else:
            units = 'oz'
        factor = data[3]
        if factor == 255:  # values are multiplied by 10
            factor = 10
        else:
            factor = 1
        weight_hi = data[4]
        weight_lo = data[5]
        weight = (weight_hi + weight_lo) / factor * sign
        return units, weight
