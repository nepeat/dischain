import os
import io
import math
from pycoin.networks import register_network
from pycoin.networks.network import Network
from pycoin.services import get_default_providers_for_netcode
from tx import Tx
from pycoin.tx.TxIn import TxIn
from pycoin.tx.TxOut import TxOut
from pycoin.tx.Spendable import Spendable
from pycoin.block import Block
from chunkers import Chunker, DiscordChunker, FileChunker
from pycoin.key.validate import netcode_and_type_for_text
from pycoin.tx.pay_to import ScriptPayToAddress, ScriptNulldata
from pycoin.serialize import b2h, b2h_rev, h2b, h2b_rev
import struct
import time

STATIC_FEE = float(os.environ.get("COIN_FEE", 0.01))
NETWORKS = [
    Network(
        "SHND", "StrongHands", "mainnet",
        b'\xbf', b'\x3f', b'\x7d',
        None, None,
        tx=Tx, block=Block
    ),
]

class CoinDaemon:
    def __init__(self, store_method="P2K"):
        self.connection = get_default_providers_for_netcode('BTC')[0].connection
        self.prepare()

    def prepare(self):
        for network in NETWORKS:
            register_network(network)

    def fee(self, size: int=0) -> int:
        return (STATIC_FEE * 1000000) * (1 + size/1024)

    @property
    def return_fee(self) -> int:
        # XXX: Make this calculate a dynamic return size too.
        return 0.01 * 1000000

    def decode_raw_tx_from_tx(self, txhash: str):
        rawtx = self.connection.getrawtransaction(txhash)
        return self.connection.decoderawtransaction(rawtx)

    def find_best_txout_for_addr(self, address: str):
        best_value = 0
        index = -1
        script = ""

        # Check for unspents.
        unspents = self.connection.listunspent(0, 9999999, [address])
        if not unspents:
            raise Exception(f"No unspent inputs found on adddress {address}.")

        # Store some unspents data.
        best_tx = unspents[0]
        tx_id = unspents[0]["txid"]
        last_tx_decoded = self.decode_raw_tx_from_tx(best_tx["txid"])

        # Parse vouts
        for output in last_tx_decoded["vout"]:
            if address not in output["scriptPubKey"].get("addresses", []):
                continue

            if output["value"] > best_value:
                best_value = output["value"]
                index = output["n"]
                script = output["scriptPubKey"]["hex"]

        if index == -1:
            raise Exception("Could not find any TXouts for address.")

        return tx_id, index, script, best_value

    def make_txs(self, address: str, chunker: Chunker, use_sends: bool=False, dont_yield=None) -> bool:
        i = 0
        if not dont_yield:
            dont_yield = []

        # We'll do our own chunking if we have to send to address hashes.
        if use_sends:
            chunker.chunk_size = 1024 * 8
        
        total_fee_needed = self.fee(chunker.data_size)

        last_tx_id, last_index, last_script, last_amount = self.find_best_txout_for_addr(address)
        last_amount = float(last_amount) * 1000000

        # Calculate hash160 for later.
        _, _, address_hash160 = netcode_and_type_for_text(address)

        # Calculate required TXes and their fees.
        needed_txes = chunker.chunk_count
        print(f"{needed_txes} TXs required.")

        required_coins = (needed_txes * total_fee_needed)
        if required_coins >= last_amount:
            raise Exception(f"Not enough coins to cover insertions. ({required_coins} or greater needed.)")

        for payload in chunker.generate_return_payloads():
            last_tx_id, last_index, last_script, last_amount = self.find_best_txout_for_addr(address)
            last_amount = float(last_amount) * 1000000
            fee_needed = self.fee(len(payload)) * 2

            # txin generate
            spendables = [Spendable.from_dict(dict(
                coin_value=last_amount,
                script_hex="0000",
                tx_hash_hex=last_tx_id,
                tx_out_index=last_index
            ))]

            txs_in = [spendable.tx_in() for spendable in spendables]

            # txout generate
            if use_sends:
                gen_function = self.generate_addr_txouts
            else:
                gen_function = self.generate_return_txouts

            txs_out = gen_function(address_hash160, payload, last_amount, fee_needed)

            # tx generate
            new_tx = Tx(1, txs_in, txs_out)

            if i not in dont_yield:
                yield new_tx.as_hex(with_time=True)

            i += 1

    def generate_addr_txouts(self, address_hash160, _payload, last_amount, fee_needed):
        txs_out = []
        payload = io.BytesIO(_payload)
        # XXX: constants file
        payload_size = 20

        # Create our payment to ourselves.
        script_pay = ScriptPayToAddress(hash160=address_hash160).script()
        txs_out.append(TxOut(last_amount - fee_needed - math.ceil((len(_payload) / payload_size) * self.return_fee), script_pay))

        # Create all the payments to the data addresses.
        # Chunking from https://github.com/vilhelmgray/FamaMonetae/blob/master/famamonetae.py
        while True:
            chunk = payload.read(payload_size)
            chunk_size = len(chunk)

            # Break once our chunk is smaller than the payload size
            if chunk_size < payload_size:
                if chunk_size == 0:
                    break

                chunk = chunk + (B'\x00') * (payload_size - chunk_size)

            script_data = ScriptPayToAddress(hash160=chunk).script()
            txs_out.append(TxOut(self.return_fee, script_data))

        return txs_out

    def generate_return_txouts(self, address_hash160, payload, last_amount, fee_needed):
        txs_out = []

        script_data = ScriptNulldata(nulldata=payload).script()
        txs_out.append(TxOut(self.return_fee, script_data))

        script_pay = ScriptPayToAddress(hash160=address_hash160).script()
        txs_out.append(TxOut(last_amount - fee_needed - self.return_fee, script_pay))
        
        return txs_out

    def read_tx(self, txhash: str):
        result = ""
        data = self.decode_raw_tx_from_tx(txhash)
        for out in data["vout"]:
            # Skip outputs greater than 0.1 coins because who uses that much coins for dust?
            if out["value"] > 0.1:
                continue
            asm = out["scriptPubKey"]["asm"]
            result += asm.split(" ")[2]

        result = h2b(result).rstrip(b"\x00")
        datalen = len(result) - 1 - (8 * 4)
        result = struct.unpack(f"!BQQQQ{datalen}s", result)

        return result

# test code, remove me later
if __name__ == "__main__":
    if "TEST_DISCORD" in os.environ:
        test_trans = DiscordChunker(
            server_id=321037402002948099,
            channel_id=321037402002948099,
            user_id=66153853824802816,
            message_id=411956284196126731,
            data="REMOVE KEBAB remove kebab you are worst turk. you are the turk idiot you are the turk smell. return to croatioa. to our croatia cousins you may come our contry. you may live in the zoo….ahahahaha ,bosnia we will never forgeve you. cetnik rascal FUck but fuck asshole turk stink bosnia sqhipere shqipare..turk genocide best day of my life. take a bath of dead turk..ahahahahahBOSNIA WE WILL GET YOU!! do not forget ww2 .albiania we kill the king , albania return to your precious mongolia….hahahahaha idiot turk and bosnian smell so bad..wow i can smell it. REMOVE KEBAB FROM THE PREMISES. you will get caught. russia+usa+croatia+slovak=kill bosnia…you will ww2/ tupac alive in serbia, tupac making album of serbia . fast rap tupac serbia. we are rich and have gold now hahahaha ha because of tupac… you are ppoor stink turk… you live in a hovel hahahaha, you live in a yurt tupac alive numbr one #1 in serbia ….fuck the croatia ,..FUCKk ashol turks no good i spit﻿ in the mouth eye of ur flag and contry. 2pac aliv and real strong wizard kill all the turk farm aminal with rap magic now we the serba rule .ape of the zoo presidant georg bush fukc the great satan and lay egg this egg hatch and bosnia wa;s born. stupid baby form the eggn give bak our clay we will crush u lik a skull of pig. serbia greattst countrey"
        )
    elif "TEST_FILE" in os.environ:
        test_trans = FileChunker(
            open(os.environ["TEST_FILE"], "rb"),
            os.path.basename(os.environ["TEST_FILE"]),
            chunk_size=1024
        )
    else:
        raise Exception("environ not set TEST_DISCORD/TEST_FILE")

    coind = CoinDaemon()

    for transaction in coind.make_txs("SaryWoh3Aqrzk9r78uG7iBSTMPfz2suvGj", test_trans, True, dont_yield=[0]):
        signed_tx = coind.connection.signrawtransaction(transaction)["hex"]
        while True:
            try:
                sent_tx = coind.connection.sendrawtransaction(signed_tx, 1)
                break
            except Exception as e:
                print("Failed to send TX. (%s)" % (e))
                time.sleep(30)
        print(sent_tx)

    # print(coind.read_tx("64e3262d4b7c6d528b972d84a6a7667bb07ba0373f68b8ee32d114101fb6b676"))