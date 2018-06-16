import os

COIN_TO_SATOSHI = 1000000
STATIC_FEE = float(os.environ.get("COIN_FEE", 0.01))

# We should not be paying more than 5 coins as fees at all.
HIGHWAY_ROBBERY = 5 * COIN_TO_SATOSHI
