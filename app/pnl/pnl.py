def calc(entry, exit_price, fee=0.002):
    return ((exit_price - entry) / entry) - fee
