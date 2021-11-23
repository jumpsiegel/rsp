from time import time, sleep
from typing import List, Tuple, Dict, Any, Optional, Union
from base64 import b64decode
import base64
import random

from algosdk.v2client.algod import AlgodClient
from algosdk.kmd import KMDClient
from algosdk import account, mnemonic
from algosdk.future import transaction
from pyteal import compileTeal, Mode, Expr
from pyteal import *
from algosdk.logic import get_application_address

import pprint

class Account:
    """Represents a private key and address for an Algorand account"""

    def __init__(self, privateKey: str) -> None:
        self.sk = privateKey
        self.addr = account.address_from_private_key(privateKey)

    def getAddress(self) -> str:
        return self.addr

    def getPrivateKey(self) -> str:
        return self.sk

    def getMnemonic(self) -> str:
        return mnemonic.from_private_key(self.sk)

    @classmethod
    def FromMnemonic(cls, m: str) -> "Account":
        return cls(mnemonic.to_private_key(m))

class PendingTxnResponse:
    def __init__(self, response: Dict[str, Any]) -> None:
        self.poolError: str = response["pool-error"]
        self.txn: Dict[str, Any] = response["txn"]

        self.applicationIndex: Optional[int] = response.get("application-index")
        self.assetIndex: Optional[int] = response.get("asset-index")
        self.closeRewards: Optional[int] = response.get("close-rewards")
        self.closingAmount: Optional[int] = response.get("closing-amount")
        self.confirmedRound: Optional[int] = response.get("confirmed-round")
        self.globalStateDelta: Optional[Any] = response.get("global-state-delta")
        self.localStateDelta: Optional[Any] = response.get("local-state-delta")
        self.receiverRewards: Optional[int] = response.get("receiver-rewards")
        self.senderRewards: Optional[int] = response.get("sender-rewards")

        self.innerTxns: List[Any] = response.get("inner-txns", [])
        self.logs: List[bytes] = [b64decode(l) for l in response.get("logs", [])]

class RPS:
    def __init__(self) -> None:
        self.ALGOD_ADDRESS = "http://localhost:4001"
        self.ALGOD_TOKEN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.FUNDING_AMOUNT = 100_000_000

        self.KMD_ADDRESS = "http://localhost:4002"
        self.KMD_TOKEN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.KMD_WALLET_NAME = "unencrypted-default-wallet"
        self.KMD_WALLET_PASSWORD = ""

        self.kmdAccounts : Optional[List[Account]] = None

        self.accountList : List[Account] = []

        self.APPROVAL_PROGRAM = b""
        self.CLEAR_STATE_PROGRAM = b""

    def waitForTransaction(
            self, client: AlgodClient, txID: str, timeout: int = 10
    ) -> PendingTxnResponse:
        lastStatus = client.status()
        lastRound = lastStatus["last-round"]
        startRound = lastRound
    
        while lastRound < startRound + timeout:
            pending_txn = client.pending_transaction_info(txID)
    
            if pending_txn.get("confirmed-round", 0) > 0:
                return PendingTxnResponse(pending_txn)
    
            if pending_txn["pool-error"]:
                raise Exception("Pool error: {}".format(pending_txn["pool-error"]))
    
            lastStatus = client.status_after_block(lastRound + 1)
    
            lastRound += 1
    
        raise Exception(
            "Transaction {} not confirmed after {} rounds".format(txID, timeout)
        )

    def getKmdClient(self) -> KMDClient:
        return KMDClient(self.KMD_TOKEN, self.KMD_ADDRESS)
    
    def getGenesisAccounts(self) -> List[Account]:
        if self.kmdAccounts is None:
            kmd = self.getKmdClient()
    
            wallets = kmd.list_wallets()
            walletID = None
            for wallet in wallets:
                if wallet["name"] == self.KMD_WALLET_NAME:
                    walletID = wallet["id"]
                    break
    
            if walletID is None:
                raise Exception("Wallet not found: {}".format(self.KMD_WALLET_NAME))
    
            walletHandle = kmd.init_wallet_handle(walletID, self.KMD_WALLET_PASSWORD)
    
            try:
                addresses = kmd.list_keys(walletHandle)
                privateKeys = [
                    kmd.export_key(walletHandle, self.KMD_WALLET_PASSWORD, addr)
                    for addr in addresses
                ]
                self.kmdAccounts = [Account(sk) for sk in privateKeys]
            finally:
                kmd.release_wallet_handle(walletHandle)
    
        return self.kmdAccounts
    
    def getTemporaryAccount(self, client: AlgodClient) -> Account:
        if len(self.accountList) == 0:
            sks = [account.generate_account()[0] for i in range(16)]
            self.accountList = [Account(sk) for sk in sks]
    
            genesisAccounts = self.getGenesisAccounts()
            suggestedParams = client.suggested_params()
    
            txns: List[transaction.Transaction] = []
            for i, a in enumerate(self.accountList):
                fundingAccount = genesisAccounts[i % len(genesisAccounts)]
                txns.append(
                    transaction.PaymentTxn(
                        sender=fundingAccount.getAddress(),
                        receiver=a.getAddress(),
                        amt=self.FUNDING_AMOUNT,
                        sp=suggestedParams,
                    )
                )
    
            txns = transaction.assign_group_id(txns)
            signedTxns = [
                txn.sign(genesisAccounts[i % len(genesisAccounts)].getPrivateKey())
                for i, txn in enumerate(txns)
            ]
    
            client.send_transactions(signedTxns)
    
            self.waitForTransaction(client, signedTxns[0].get_txid())
    
        return self.accountList.pop()
    
    def getAlgodClient(self) -> AlgodClient:
        return AlgodClient(self.ALGOD_TOKEN, self.ALGOD_ADDRESS)

    def getBalances(self, client: AlgodClient, account: str) -> Dict[int, int]:
        balances: Dict[int, int] = dict()
    
        accountInfo = client.account_info(account)
    
        # set key 0 to Algo balance
        balances[0] = accountInfo["amount"]
    
        assets: List[Dict[str, Any]] = accountInfo.get("assets", [])
        for assetHolding in assets:
            assetID = assetHolding["asset-id"]
            amount = assetHolding["amount"]
            balances[assetID] = amount
    
        return balances

    def fullyCompileContract(self, client: AlgodClient, contract: Expr) -> bytes:
        teal = compileTeal(contract, mode=Mode.Application, version=5)
        response = client.compile(teal)
        return b64decode(response["result"])

    # helper function that formats global state for printing
    def format_state(self, state):
        formatted = {}
        for item in state:
            key = item['key']
            value = item['value']
            formatted_key = base64.b64decode(key).decode('utf-8')
            if value['type'] == 1:
                # byte string
                if formatted_key == 'voted':
                    formatted_value = base64.b64decode(value['bytes']).decode('utf-8')
                else:
                    formatted_value = value['bytes']
                formatted[formatted_key] = formatted_value
            else:
                # integer
                formatted[formatted_key] = value['uint']
        return formatted
    
    # helper function to read app global state
    def read_global_state(self, client, addr, app_id):
        results = client.account_info(addr)
        apps_created = results['created-apps']
        for app in apps_created:
            if app['id'] == app_id:
                return self.format_state(app['params']['global-state'])
        return {}
        
    def getContracts(self, client: AlgodClient) -> Tuple[bytes, bytes]:
        if len(self.APPROVAL_PROGRAM) == 0:
            def approval_program(): 
                player1_account_key = Bytes("player1_account")
                player1_amount_key = Bytes("player1_amount")
                player2_account_key = Bytes("player2_account")
                player2_amount_key = Bytes("player2_amount")

                on_create = Seq(
                    App.globalPut(player1_account_key, Global.zero_address()),
                    App.globalPut(player2_account_key, Global.zero_address()),
                    Approve(),
                )

                on_bid_txn_index = Txn.group_index() - Int(1)

                on_bid = Seq(
                    Assert(
                        And(
                            Gtxn[on_bid_txn_index].type_enum() == TxnType.Payment,
                            Gtxn[on_bid_txn_index].sender() == Txn.sender(),
                            Gtxn[on_bid_txn_index].receiver()
                            == Global.current_application_address(),
                            Gtxn[on_bid_txn_index].amount() >= Global.min_txn_fee(),
                        )
                    ),
                    Cond(
                        [App.globalGet(player1_account_key) == Global.zero_address(),
                         Seq(
                             App.globalPut(player1_amount_key, Gtxn[on_bid_txn_index].amount()),
                             App.globalPut(player1_account_key, Gtxn[on_bid_txn_index].sender()),
                             Approve(),
                         )],
                        [App.globalGet(player1_account_key) == Gtxn[on_bid_txn_index].sender(), 
                         Seq(
                            App.globalPut(player1_amount_key, App.globalGet(player1_amount_key) + Gtxn[on_bid_txn_index].amount()),
                            Approve(),
                        )],
                        [ App.globalGet(player2_account_key) == Global.zero_address(),
                        Seq(
                            App.globalPut(player2_amount_key, Gtxn[on_bid_txn_index].amount()),
                            App.globalPut(player2_account_key, Gtxn[on_bid_txn_index].sender()),
                            Approve(),
                        )],
                        [App.globalGet(player2_account_key) == Gtxn[on_bid_txn_index].sender(),
                         Seq(
                            App.globalPut(player2_amount_key, App.globalGet(player2_amount_key) + Gtxn[on_bid_txn_index].amount()),
                            Approve(),
                        )]
                    ),
                    Reject(),
                )

                @Subroutine(TealType.none)
                def closeAccountTo(account: Expr) -> Expr:
                    return If(Balance(Global.current_application_address()) != Int(0)).Then(
                        Seq(
                            InnerTxnBuilder.Begin(),
                            InnerTxnBuilder.SetFields(
                                {
                                    TxnField.type_enum: TxnType.Payment,
                                    TxnField.close_remainder_to: account,
                                }
                            ),
                            InnerTxnBuilder.Submit(),
                        )
                    )

                on_delete = Seq(
#                    Seq(
#                        # the auction has not yet started, it's ok to delete
#                            Assert(
#                                Or(
#                                    # sender must either be the seller or the auction creator
#                                    Txn.sender() == App.globalGet(seller_key),
#                                    Txn.sender() == Global.creator_address(),
#                                )
#                            ),
#                        # if the auction contract still has funds, send them all to the seller
#                        closeAccountTo(App.globalGet(seller_key)),
#                        Approve(),
#                    ),
#                    Reject(),
#
                    # All our money is lost... sorry, tell josh to finish this
                    Approve(),
                )

                on_call_method = Txn.application_args[0]
                on_call = Cond(
                    [on_call_method == Bytes("bid"), on_bid]
                )

                program = Cond(
                    [Txn.application_id() == Int(0), on_create],
                    [Txn.on_completion() == OnComplete.NoOp, on_call],
                    [Txn.on_completion() == OnComplete.DeleteApplication, on_delete],
                    [
                        Or(
                            Txn.on_completion() == OnComplete.OptIn,
                            Txn.on_completion() == OnComplete.CloseOut,
                            Txn.on_completion() == OnComplete.UpdateApplication,
                        ),
                        Reject(),
                    ],
                )
                return program
        
            def clear_state_program():
                return Approve()
        
            self.APPROVAL_PROGRAM = self.fullyCompileContract(client, approval_program())
            self.CLEAR_STATE_PROGRAM = self.fullyCompileContract(client, clear_state_program())

            with open("rps_approval.teal", "w") as f:
                compiled = compileTeal(approval_program(), mode=Mode.Application, version=5)
                f.write(compiled)

            with open("rps_clear_state.teal", "w") as f:
                compiled = compileTeal(clear_state_program(), mode=Mode.Application, version=5)
                f.write(compiled)
    
        return self.APPROVAL_PROGRAM, self.CLEAR_STATE_PROGRAM

    def createRPSApp(
        self,
        client: AlgodClient,
        sender: Account,
    ) -> int:
        approval, clear = self.getContracts(client)
    
        globalSchema = transaction.StateSchema(num_uints=2, num_byte_slices=2)
        localSchema = transaction.StateSchema(num_uints=0, num_byte_slices=0)
    
        app_args = [ ]
    
        txn = transaction.ApplicationCreateTxn(
            sender=sender.getAddress(),
            on_complete=transaction.OnComplete.NoOpOC,
            approval_program=approval,
            clear_program=clear,
            global_schema=globalSchema,
            local_schema=localSchema,
            app_args=app_args,
            sp=client.suggested_params(),
        )
    
        signedTxn = txn.sign(sender.getPrivateKey())
    
        client.send_transaction(signedTxn)
    
        response = self.waitForTransaction(client, signedTxn.get_txid())
        assert response.applicationIndex is not None and response.applicationIndex > 0
        return response.applicationIndex

    def placeBid(self, client: AlgodClient, appID: int, bidder: Account, bidAmount: int) -> None:
        appAddr = get_application_address(appID)
    
        suggestedParams = client.suggested_params()
    
        payTxn = transaction.PaymentTxn(
            sender=bidder.getAddress(),
            receiver=appAddr,
            amt=bidAmount,
            sp=suggestedParams,
        )
    
        appCallTxn = transaction.ApplicationCallTxn(
            sender=bidder.getAddress(),
            index=appID,
            on_complete=transaction.OnComplete.NoOpOC,
            app_args=[b"bid"],
            sp=suggestedParams,
        )

        transaction.assign_group_id([payTxn, appCallTxn])
    
        signedPayTxn = payTxn.sign(bidder.getPrivateKey())
        signedAppCallTxn = appCallTxn.sign(bidder.getPrivateKey())
    
        client.send_transactions([signedPayTxn, signedAppCallTxn])
    
        self.waitForTransaction(client, appCallTxn.get_txid())
    
    # Rock-Scissors-Paper on the algorand block chain
    def simple_rps(self):
        client = self.getAlgodClient()

        print("Generating the player accounts...")
        player1 = self.getTemporaryAccount(client)

        print("player1 assets")
        pprint.pprint(self.getBalances(client, player1.getAddress()))

        player2 = self.getTemporaryAccount(client)

        print("player2 assets")
        pprint.pprint(self.getBalances(client, player2.getAddress()))

        print("Creating the RPS app")
        appID = self.createRPSApp(
            client=client,
            sender=player1
        )
        print("appID = " + str(appID))

        print("application state")
        pprint.pprint(self.read_global_state(client, player1.getAddress(), appID))

        print("application assets")
        pprint.pprint(self.getBalances(client, get_application_address(appID)))

        print("player 1 bids 300000")
        self.placeBid(client, appID, player1, 300000)

        print("player1 assets")
        pprint.pprint(self.getBalances(client, player1.getAddress()))
        print("application assets")
        pprint.pprint(self.getBalances(client, get_application_address(appID)))

        print("player 2 bids 301500")
        self.placeBid(client, appID, player2, 301500)

        print("player2 assets")
        pprint.pprint(self.getBalances(client, player2.getAddress()))
        print("application assets")
        pprint.pprint(self.getBalances(client, get_application_address(appID)))

        print("player 1 calls for 1500")
        self.placeBid(client, appID, player1, 1500)

        print("player1 assets")
        pprint.pprint(self.getBalances(client, player1.getAddress()))
        print("application assets")
        pprint.pprint(self.getBalances(client, get_application_address(appID)))

        print("application state")
        pprint.pprint(self.read_global_state(client, player1.getAddress(), appID))

        moves = [ "rock", "scissors", "paper" ]
        player1_move = bytes(moves[random.randint(0, 2)] + str(random.random()), "ascii")
        player1_move_hash = base64.b64encode(hashlib.sha256(player1_move).digest()).decode()

        print("Player1 move (" + str(player1_move) + ") hashes to ( " + player1_move_hash)

        player2_move = bytes(moves[random.randint(0, 2)] + str(random.random()), "ascii")
        player2_move_hash = base64.b64encode(hashlib.sha256(player2_move).digest()).decode()

        print("Player2 move (" + str(player2_move) + ") hashes to ( " + player2_move_hash)
        
        # 5. Player 1 throws down hash of move
        #    reject if hash already submitted
        # 6. Player 2 throws down hash of move
        #    reject if hash already submitted
        # 7. Player 1 throws down move
        #    reject if hash of move does not match what was previously submitted
        # 8. Player 2 throws down move
        #    reject if hash of move does not match what was previously submitted
        # 9. Payout to winner


rps = RPS()
rps.simple_rps()
