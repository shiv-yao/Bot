
import torch
import torch.nn as nn
import torch.optim as optim

class PPO(nn.Module):
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(
            nn.Linear(5,64),
            nn.ReLU(),
            nn.Linear(64,32),
            nn.ReLU(),
            nn.Linear(32,1),
            nn.Sigmoid()
        )
    def forward(self,x):
        return self.net(x)

MODEL=PPO()
OPT=optim.Adam(MODEL.parameters(),lr=1e-3)

def train(x,reward):
    x=torch.tensor(x,dtype=torch.float32)
    y=torch.tensor([reward],dtype=torch.float32)
    pred=MODEL(x)
    loss=((pred-y)**2).mean()
    OPT.zero_grad()
    loss.backward()
    OPT.step()
