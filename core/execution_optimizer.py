
class ExecutionOptimizer:
    def choose(self, alpha, impact):
        if alpha > 150 or impact > 0.1:
            return "jito"
        return "rpc"
