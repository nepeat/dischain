import os
import io
import math
import random
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
from constants import STATIC_FEE, HIGHWAY_ROBBERY

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
        return (STATIC_FEE * 1000000) * (1 + size/1024) * 2

    @property
    def return_fee(self) -> int:
        # XXX: Make this calculate a dynamic return size too.
        return 0.01 * 1000000

    def decode_raw_tx_from_tx(self, txhash: str):
        rawtx = self.connection.getrawtransaction(txhash)
        return self.connection.decoderawtransaction(rawtx)

    def get_balance(self, address: str):
        amount = 0

        unspents = self.connection.listunspent(0, 9999999, [address])
        for account in unspents:
            amount += account["amount"]

        return amount

    def get_spendables(self, address: str, amount: int=None, get_all=False):
        spendables = []

        # Safety for some very good code.
        if not amount and not get_all and amount <= 0:
            raise Exception("Amount or get_all flag must be given.")

        # Check for unspents.
        unspents = self.connection.listunspent(0, 9999999, [address])
        if not unspents:
            raise Exception(f"No unspent inputs found on adddress {address}.")

        for tx in unspents:
            spendables.append(Spendable.from_dict(dict(
                coin_value=tx["amount"] * 1000000,
                script_hex="0000",
                tx_hash_hex=tx["txid"],
                tx_out_index=tx["vout"]
            )))

            if not get_all and (amount < 0):
                break

            if amount:
                amount -= tx["amount"] * 1000000

        return spendables

    def make_txs(self, address: str, chunker: Chunker, use_sends: bool=False) -> bool:
        # We'll do our own chunking if we have to send to address hashes.
        if use_sends:
            chunker.chunk_size = 1024 * 128

        # Calculate hash160 for later.
        _, _, address_hash160 = netcode_and_type_for_text(address)

        # Calculate required TXes and their fees.
        total_fee_needed = self.fee(chunker.data_size)
        last_amount = float(self.get_balance(address))

        # Pick the generation function appropiate for our coin.
        if use_sends:
            gen_function = self.generate_addr_txouts
        else:
            gen_function = self.generate_return_txouts

        print(f"{chunker.chunk_count} TXs will be made.")
        print(f"{total_fee_needed / 1000000} coins will be burnt.")

        if total_fee_needed / 1000000 >= last_amount:
            raise Exception(f"Not enough coins to cover insertions. ({total_fee_needed / 1000000} or greater needed.)")

        for nonce, payload in chunker.generate_return_payloads():
            last_amount = float(self.get_balance(address)) * 1000000
            fee_needed = self.fee(len(payload))

            # txout generate stage 1
            txs_out = []
            txs_out.extend(gen_function(address_hash160, payload, fee_needed))
            txs_out.extend(self.create_change(address, txs_out, last_amount))

            # txin generate
            total_amount_txout = sum(txo.coin_value for txo in txs_out)
            remainder_txout = total_amount_txout + fee_needed
            total_amount_txin = 0
            txs_in = []
            # We sort by reverse if we want to devour the latest large TXIn chunk for making change addresses.
            # Sort by random if we have enough spare change.
            creates_change_addrs = (total_amount_txout / 1000000) > 100  # XXX/HACK This is assuming spending is not above 100 coins.
            spendables = self.get_spendables(address, total_amount_txout)
            if creates_change_addrs:
                spendables.sort(key=lambda _: _.coin_value, reverse=creates_change_addrs)
            else:
                random.shuffle(spendables)
            for spendable in spendables:
                print(f"OUT {remainder_txout / 1000000}; CV {spendable.coin_value / 1000000}")
                if remainder_txout < 0:
                    break
                txs_in.append(spendable.tx_in())
                total_amount_txin += spendable.coin_value
                remainder_txout -= spendable.coin_value

            # We need to round the change. / txout generate stage 2
            round_change_tx, round_change_amount = self.round_change(address_hash160, total_amount_txin, total_amount_txout, fee_needed)
            # XXX/HACK This fixes a bug where the round change somehow gets to negative values.
            # I don't even know how that even happens... yet.
            if round_change_tx.coin_value > 0:
                txs_out.append(round_change_tx)
                total_amount_txout += round_change_amount

            # tx sanity checks
            total_combined = total_amount_txin - total_amount_txout

            print(f"[tx#{nonce}] IN {total_amount_txin / 1000000}")
            print(f"[tx#{nonce}] OUT {total_amount_txout / 1000000}")
            print(f"[tx#{nonce}] FEE {total_combined / 1000000}")

            if total_combined < 0:
                print(total_amount_txin)
                print(total_amount_txout)
                raise Exception(f"[tx#{nonce}] negative transaction ({total_combined / 1000000})")
            elif total_combined > HIGHWAY_ROBBERY:
                print(total_amount_txin)
                print(total_amount_txout)
                raise Exception(f"[tx#{nonce}] overpaying fees ({total_combined / 1000000})")

            # tx generate
            new_tx = Tx(1, txs_in, txs_out)

            yield new_tx.as_hex(with_time=True)

    def create_change(self, address: str, existing_txouts, balance: int):
        new_txouts = []
        change_size = 100 * 1000000  # 100 coins
        total_out = sum(txo.coin_value for txo in existing_txouts)
        balance = balance - total_out

        spendables = self.get_spendables(address, get_all=True)

        # Prune out spendables that we will spend.
        for spendable in spendables.copy():
            if total_out == 0:
                continue

            if total_out < 0:
                raise Exception(f"total_out is negative. ({total_out})")

            if spendable.coin_value > total_out:
                spendables.remove(spendable)
                break
            elif total_out > spendable.coin_value:
                spendables.remove(spendable)
                total_out -= spendable.coin_value

        # XXX: Find a way to cleanly unify this.
        _, _, address_hash160 = netcode_and_type_for_text(address)

        # 15 TXs in our own wallet should be a good buffer to have.
        if len(spendables) >= 15:
            return []

        # Create our payment to ourselves if we still have the coins.
        if (balance - (change_size * len(spendables))) < change_size:
            print("Warning: Low balance in address.")
            balance -= (change_size * len(spendables))

        for x in range(len(spendables), 50):
            balance -= change_size
            if balance < 0:
                break

            script_pay = ScriptPayToAddress(hash160=address_hash160).script()
            new_txouts.append(TxOut(change_size, script_pay))

        return new_txouts

    def round_change (self, address_hash160: str, txin_amount: int, txout_amount, fee_needed: int):
        total_payment = txin_amount - txout_amount - fee_needed

        script_pay = ScriptPayToAddress(hash160=address_hash160).script()
        return TxOut(total_payment, script_pay), total_payment

    def generate_addr_txouts(self, address_hash160, _payload, fee_needed):
        txs_out = []
        payload = io.BytesIO(_payload)
        # XXX: constants file
        payload_size = 20

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

    def generate_return_txouts(self, address_hash160, payload, fee_needed):
        txs_out = []

        script_data = ScriptNulldata(nulldata=payload).script()
        txs_out.append(TxOut(self.return_fee, script_data))
        
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
    dont_yield = list(range(0,259))

    if "TEST_DISCORD" in os.environ:
        test_trans = DiscordChunker(
            server_id=321037402002948099,
            channel_id=321037402002948099,
            user_id=66153853824802816,
            message_id=411956284196126731,
            data="REMOVE KEBAB remove kebab you are worst turk. you are the turk idiot you are the turk smell. return to croatioa. to our croatia cousins you may come our contry. you may live in the zoo….ahahahaha ,bosnia we will never forgeve you. cetnik rascal FUck but fuck asshole turk stink bosnia sqhipere shqipare..turk genocide best day of my life. take a bath of dead turk..ahahahahahBOSNIA WE WILL GET YOU!! do not forget ww2 .albiania we kill the king , albania return to your precious mongolia….hahahahaha idiot turk and bosnian smell so bad..wow i can smell it. REMOVE KEBAB FROM THE PREMISES. you will get caught. russia+usa+croatia+slovak=kill bosnia…you will ww2/ tupac alive in serbia, tupac making album of serbia . fast rap tupac serbia. we are rich and have gold now hahahaha ha because of tupac… you are ppoor stink turk… you live in a hovel hahahaha, you live in a yurt tupac alive numbr one #1 in serbia ….fuck the croatia ,..FUCKk ashol turks no good i spit﻿ in the mouth eye of ur flag and contry. 2pac aliv and real strong wizard kill all the turk farm aminal with rap magic now we the serba rule .ape of the zoo presidant georg bush fukc the great satan and lay egg this egg hatch and bosnia wa;s born. stupid baby form the eggn give bak our clay we will crush u lik a skull of pig. serbia greattst countrey",
            dont_yield=dont_yield,
        )
    elif "TEST_FILE" in os.environ:
        test_trans = FileChunker(
            open(os.environ["TEST_FILE"], "rb"),
            os.path.basename(os.environ["TEST_FILE"]),
            chunk_size=1024,
            dont_yield=dont_yield,
        )
    else:
        raise Exception("environ not set TEST_DISCORD/TEST_FILE")

    coind = CoinDaemon()

    for transaction in coind.make_txs("SNhe3fDaAkcGyh27D1CvPq2kfGiSUqa6Q2", test_trans, True, dont_yield=list(range(0,0))):
        signed_tx = coind.connection.signrawtransaction(transaction)["hex"]
        if "PRINT_TX" in os.environ:
            print(signed_tx)

        while True:
            try:
                if "SEND_TX" in os.environ:
                    sent_tx = coind.connection.sendrawtransaction(signed_tx, 1)
                    print(sent_tx)
                else:
                    print(f"got tx of len {len(signed_tx)} but not sending")
                time.sleep(5)
                break
            except Exception as e:
                print("Failed to send TX. (%s)" % (e))
                time.sleep(15)

    # print(coind.read_tx("64e3262d4b7c6d528b972d84a6a7667bb07ba0373f68b8ee32d114101fb6b676"))