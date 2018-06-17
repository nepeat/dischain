import struct
import math
import os
import crcmod


class Chunker:
    __slots__ = ["chunk_size", "dont_yield"]

    def __init__(self, chunk_size: int=80, dont_yield=None):
        self.chunk_size = chunk_size
        if not dont_yield:
            self.dont_yield = []
        else:
            self.dont_yield = dont_yield

    @property
    def data_size(self) -> int:
        raise NotImplementedError

    @property
    def chunk_count(self) -> int:
        return math.ceil(self.data_size / self.chunk_size)

    def chunk_data(self):
        raise NotImplementedError

    def generate_return_payloads(self):
        raise NotImplementedError


class DiscordChunker(Chunker):
    __slots__ = ["chunk_size", "server_id", "channel_id", "user_id", "message_id", "data"]

    HEADER_SIZE = 1 + 8
    INITIAL_HEADER_SIZE = HEADER_SIZE + (8 * 3)

    def __init__(self, server_id: int, channel_id: int, user_id: int, message_id: int, data: str, *args, **kwargs):
        self.server_id = server_id
        self.channel_id = channel_id
        self.user_id = user_id
        self.message_id = message_id

        if isinstance(data, str):
            self.data = data.encode("utf8")
        else:
            self.data = data

        super().__init__(*args, **kwargs)

    @property
    def data_size(self):
        return len(self.data)

    def chunk_data(self):
        data_copy = bytes(self.data)
        header_chunk_size = self.chunk_size - self.HEADER_SIZE
        nonce = 0

        # Create the header chunk manually first.
        yield nonce, data_copy[0:self.chunk_size - self.INITIAL_HEADER_SIZE]
        data_copy = data_copy[self.chunk_size - self.INITIAL_HEADER_SIZE:]
        nonce += 1

        # Create the rest of the chunks automatically
        for i in range(0, len(data_copy), header_chunk_size):
            yield nonce, data_copy[i:i + header_chunk_size]
            nonce += 1

    def generate_return_payloads(self):
        # Sanity check
        if self.chunk_count > 255:
            raise Exception(f"Chunk count exceeds nonce limit. ({self.chunk_count > 255})")

        for nonce, chunk in self.chunk_data():
            if nonce in self.dont_yield:
                continue

            chunk_len = len(chunk)

            if nonce == 0:
                payload = struct.pack(f"!BQQQQ{chunk_len}s", nonce, self.message_id, self.server_id, self.channel_id, self.user_id, chunk)
            else:
                payload = struct.pack(f"!BQ{chunk_len}s", nonce, self.message_id, chunk)

            yield nonce, payload


class FileChunker(Chunker):
    __slots__ = ["chunk_size", "file_obj", "crchash", "filename"]

    HEADER_SIZE = 1 + 8

    def __init__(self, file_obj, filename: str, *args, **kwargs):
        self.file_obj = file_obj
        self.filename = filename

        self.crchash = self.calculate_crc()

        super().__init__(*args, **kwargs)

    def calculate_crc(self):
        crc = crcmod.predefined.Crc('crc-64')
        for chunk in iter(lambda: self.file_obj.read(4096), b""):
            crc.update(chunk)

        # Assumption: We only call calculate_crc once at __init__ and we aren't seeked elsewhere.
        self.file_obj.seek(0)

        return crc.crcValue

    @property
    def data_size(self):
        current_position = self.file_obj.tell()
        self.file_obj.seek(0, os.SEEK_END)
        file_size = self.file_obj.tell()
        self.file_obj.seek(current_position, os.SEEK_SET)

        return file_size

    def chunk_data(self):
        # Variable chunk sizes that may change with different altcoin implementations.
        chunk_size_minus_header = self.chunk_size - self.HEADER_SIZE

        # Create the rest of the chunks automatically
        nonce = 0
        for i in range(0, self.data_size, chunk_size_minus_header):
            yield nonce, self.file_obj.read(chunk_size_minus_header)
            nonce += 1

    def generate_return_payloads(self):
        # Sanity check
        # XXX make uint32 constant
        if self.chunk_count > 4294967295:
            raise Exception(f"Chunk count exceeds nonce limit. ({self.chunk_count > 4294967295})")

        for nonce, chunk in self.chunk_data():
            if nonce in self.dont_yield:
                continue

            # Special handling for chunk 0 to add the filename to it.
            if nonce == 0:
                chunk = self.filename.encode("utf8") +  b";" + chunk

            chunk_len = len(chunk)

            # Filename is in the first chunk.
            yield nonce, struct.pack(f"!IQ{chunk_len}s", nonce, self.crchash, chunk)
