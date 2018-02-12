import struct
import math

HEADER_SIZE = 1 + 8
INITIAL_HEADER_SIZE = HEADER_SIZE + (8 * 3)


class Chunker:
    __slots__ = ["data", "chunk_size"]

    def __init__(self, chunk_size=80):
        self.chunk_size = chunk_size

    @property
    def data_size(self):
        raise NotImplementedError

    def chunk_count(self):
        return math.ceil(self.data_size / self.chunk_size)

    def chunk_data(self):
        raise NotImplementedError

    def generate_return_payloads(self):
        raise NotImplementedError

class DiscordChunker(Chunker):
    __slots__ = ["server_id", "channel_id", "user_id", "message_id", "data", "chunk_size"]

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
        header_chunk_size = self.chunk_size - HEADER_SIZE
        chunks = []

        # Create the header chunk manually first.
        chunks.append(data_copy[0:self.chunk_size - INITIAL_HEADER_SIZE])
        data_copy = data_copy[self.chunk_size - INITIAL_HEADER_SIZE:]

        # Create the rest of the chunks automatically
        for i in range(0, len(data_copy), header_chunk_size):
            chunks.append(data_copy[i:i + header_chunk_size])

        return chunks

    def generate_return_payloads(self):
        nonce = 0
        chunks = self.chunk_data()

        # Sanity check
        if len(chunks) > 255:
            raise Exception(f"Chunk count exceeds nonce limit. ({len(chunks) > 255})")

        for chunk in chunks:
            chunk_len = len(chunk)

            if nonce == 0:
                payload = struct.pack(f"!BQQQQ{chunk_len}s", nonce, self.message_id, self.server_id, self.channel_id, self.user_id, chunk)
            else:
                payload = struct.pack(f"!BQ{chunk_len}s", nonce, self.message_id, chunk)

            yield payload

            nonce += 1