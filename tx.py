import io

from pycoin.tx.Tx import Tx
from pycoin.serialize.bitcoin_streamer import stream_struct
from pycoin.serialize import b2h, b2h_rev, h2b, h2b_rev

import time

class Tx(Tx):
    """
        Patched version of Tx that includes time in the TX for SHND.
    """

    def as_hex(self, include_unspents=False, include_witness_data=True, with_time=False):
        """Return the transaction as hex."""
        return b2h(self.as_bin(
            include_unspents=include_unspents, include_witness_data=include_witness_data, with_time=with_time))

    def as_bin(self, include_unspents=False, include_witness_data=True, with_time=False):
        """Return the transaction as binary."""
        f = io.BytesIO()
        self.stream(f, include_unspents=include_unspents, include_witness_data=include_witness_data, with_time=with_time)
        return f.getvalue()

    def stream(self, f, blank_solutions=False, include_unspents=False, include_witness_data=True, with_time=False):
        """Stream a Bitcoin transaction Tx to the file-like object f."""
        include_witnesses = include_witness_data and self.has_witness_data()
        stream_struct("L", f, self.version)
        if with_time:
            stream_struct("L", f, int(time.time()))
        if include_witnesses:
            f.write(b'\0\1')
        stream_struct("I", f, len(self.txs_in))
        for t in self.txs_in:
            t.stream(f, blank_solutions=blank_solutions)
        stream_struct("I", f, len(self.txs_out))
        for t in self.txs_out:
            t.stream(f)
        if include_witnesses:
            for tx_in in self.txs_in:
                witness = tx_in.witness
                stream_struct("I", f, len(witness))
                for w in witness:
                    stream_bc_string(f, w)
        stream_struct("L", f, self.lock_time)
        if include_unspents and not self.missing_unspents():
            self.stream_unspents(f)