import os
from pycoin.networks import register_network
from pycoin.networks.network import Network
from pycoin.services import get_default_providers_for_netcode
from tx import Tx
from pycoin.tx.TxIn import TxIn
from pycoin.tx.TxOut import TxOut
from pycoin.tx.Spendable import Spendable
from pycoin.block import Block
from transactions import Transaction
from pycoin.key.validate import netcode_and_type_for_text
from pycoin.tx.pay_to import ScriptPayToAddress, ScriptNulldata
from pycoin.serialize import b2h, b2h_rev, h2b, h2b_rev

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
    def __init__(self, return_size: int=80):
        self.connection = get_default_providers_for_netcode('BTC')[0].connection
        self.return_size = return_size
        self.prepare()

    def prepare(self):
        for network in NETWORKS:
            register_network(network)

    @property
    def fee(self) -> int:
        # XXX: Make this calculate a dynamic fee for other networks.
        return STATIC_FEE * 1000000

    @property
    def return_fee(self) -> int:
        # XXX: Make this calculate a dynamic return size too.
        return 0.01 * 1000000

    def decode_raw_tx_from_tx(self, txhash: str):
        rawtx = self.connection.getrawtransaction(txhash)
        return self.connection.decoderawtransaction(rawtx)

    def find_best_txout_for_addr(self, address: str, vouts):
        best_value = 0
        index = -1
        script = ""

        for output in vouts:
            if address not in output["scriptPubKey"].get("addresses", []):
                continue

            if output["value"] > best_value:
                best_value = output["value"]
                index = output["n"]
                script = output["scriptPubKey"]["hex"]

        if index == -1:
            raise Exception("Could not find any TXouts for address.")

        return index, script, best_value

    def make_return_txs(self, address: str, transaction: Transaction) -> bool:
        # Check for unspents.
        unspents = self.connection.listunspent(0, 9999999, [address])
        if not unspents:
            raise Exception(f"No unspent inputs found on adddress {address}.")

        # Store some unspents data.
        last_tx_in = unspents[0]
        last_tx_id = unspents[0]["txid"]
        last_tx_decoded = self.decode_raw_tx_from_tx(last_tx_in["txid"])

        last_index, last_script, last_amount = self.find_best_txout_for_addr(address, last_tx_decoded["vout"])
        last_amount = float(last_amount) * 1000000

        # Calculate hash160 for later.
        _, _, address_hash160 = netcode_and_type_for_text(address)

        # Calculate required TXes and their fees.
        needed_txes = transaction.chunk_count(self.return_size)
        print(f"{needed_txes} TXs required.")

        required_coins = (needed_txes * self.fee)
        if required_coins >= last_amount:
            raise Exception(f"Not enough coins to cover insertions. ({required_coins} or greater needed.)")

        for payload in transaction.generate_return_payloads(self.return_size):
            # Generate the txin
            spendables = [Spendable.from_dict(dict(
                coin_value=last_amount,
                script_hex=last_script,
                tx_hash_hex=last_tx_id,
                tx_out_index=last_index
            ))]

            txs_in = [spendable.tx_in() for spendable in spendables]

            # txout with return
            txs_out = []

            script_data = ScriptNulldata(nulldata=payload).script()
            txs_out.append(TxOut(self.return_fee, script_data))

            script_pay = ScriptPayToAddress(hash160=address_hash160).script()
            txs_out.append(TxOut(last_amount - self.fee, script_pay))

            new_tx = Tx(1, txs_in, txs_out)

            last_tx_id = new_tx.id()
            last_amount = last_amount - self.fee - self.return_fee
            last_script = b2h(script_pay)
            last_index = 1

            print(new_tx.as_hex(with_time=True), end="\n\n")

"""
CTransaction(hash=ab2a93f707, nTime=1518289602, ver=1, vin.size=1, vout.size=2, nLockTime=0)
    CTxIn(COutPoint(7e44297cee, 1), scriptSig=3045022025dc8e798d1b059b)
    CTxOut(nValue=498.99, scriptPubKey=OP_DUP OP_HASH160 4898f3c21d254866cf7c5ed8c677a912d4bb1c4b OP_EQUALVERIFY OP_CHECKSIG)
    CTxOut(nValue=1.00, scriptPubKey=OP_DUP OP_HASH160 13e0b53a5e71e07d11da330d6bce7e491a06335a OP_EQUALVERIFY OP_CHECKSIG)
"""

# test code, remove me later
if __name__ == "__main__":
    test_trans = Transaction(
        server_id=321037402002948099,
        channel_id=321037402002948099,
        user_id=66153853824802816,
        message_id=411956284196126731,
        data="REMOVE KEBAB remove kebab you are worst turk. you are the turk idiot you are the turk smell. return to croatioa. to our croatia cousins you may come our contry. you may live in the zoo….ahahahaha ,bosnia we will never forgeve you. cetnik rascal FUck but fuck asshole turk stink bosnia sqhipere shqipare..turk genocide best day of my life. take a bath of dead turk..ahahahahahBOSNIA WE WILL GET YOU!! do not forget ww2 .albiania we kill the king , albania return to your precious mongolia….hahahahaha idiot turk and bosnian smell so bad..wow i can smell it. REMOVE KEBAB FROM THE PREMISES. you will get caught. russia+usa+croatia+slovak=kill bosnia…you will ww2/ tupac alive in serbia, tupac making album of serbia . fast rap tupac serbia. we are rich and have gold now hahahaha ha because of tupac… you are ppoor stink turk… you live in a hovel hahahaha, you live in a yurt tupac alive numbr one #1 in serbia ….fuck the croatia ,..FUCKk ashol turks no good i spit﻿ in the mouth eye of ur flag and contry. 2pac aliv and real strong wizard kill all the turk farm aminal with rap magic now we the serba rule .ape of the zoo presidant georg bush fukc the great satan and lay egg this egg hatch and bosnia wa;s born. stupid baby form the eggn give bak our clay we will crush u lik a skull of pig. serbia greattst countrey"
    )

    coind = CoinDaemon(1024)
    coind.make_return_txs("SP774U2L9YkYNyN7HWnuwr17EKuF3PavKZ", test_trans)