def compute_score(alpha, weights):
    return sum(alpha[k] * weights.get(k, 0) for k in alpha)
