

Rock-Scissors-Paper on the algorand block chain

1. Create the rsp app
2. Player 1 Bids
3. Player 2 Bids/Calls
4. Player 1 Bids/Calls
5. Player 1 throws down hash of move
   reject if hash already submitted
6. Player 2 throws down hash of move
   reject if hash already submitted
7. Player 1 throws down move
   reject if hash of move does not match what was previously submitted
8. Player 2 throws down move
   reject if hash of move does not match what was previously submitted
9. Payout to winner


--

bring up sandbox

./sandbox up nightly

Set up venv (one time):
 * `python3 -m venv venv`

Active venv:
 * `. venv/bin/activate` (if your shell is bash/zsh)

Install dependencies:
* `pip install -r requirements.txt`

