
import torch
from app.rl.ppo import MODEL

def score(token):
    feats=[len(token)%5,1,0,1,0]
    x=torch.tensor(feats,dtype=torch.float32)
    return MODEL(x).item()
