import torch, itertools, functools
import torch.nn as nn

from torch.utils.checkpoint import checkpoint
from torch_dimcheck import dimchecked
from .utils import cut_to_match, size_is_pow2
from .ops import TrivialUpsample, TrivialDownsample, NoOp, UGroupNorm, u_group_norm
from .blocks import UnetDownBlock, UnetUpBlock, ThinUnetDownBlock, ThinUnetUpBlock

fat_setup = {
    'gate': nn.PReLU,
    'norm': nn.InstanceNorm2d,
    'upsample': TrivialUpsample,
    'downsample': TrivialDownsample,
    'down_block': UnetDownBlock,
    'up_block': UnetUpBlock,
    'dropout': NoOp,
    'padding': False,
    'bias': True
}

thin_setup = {
    'gate': nn.PReLU,
    'norm': nn.InstanceNorm2d,
    'upsample': TrivialUpsample,
    'downsample': TrivialDownsample,
    'down_block': ThinUnetDownBlock,
    'up_block': ThinUnetUpBlock,
    'dropout': NoOp,
    'padding': False,
    'bias': True
}

def checkpointed(cls):
    assert issubclass(cls, torch.nn.Module)

    #@functools.wraps(cls)
    class Checkpointed(cls):
        def forward(self, *args, **kwargs):
            super_fwd = super(Checkpointed, self).forward
            if any((torch.is_tensor(arg) and arg.requires_grad) for arg in args):
                return checkpoint(super_fwd, *args, **kwargs)
            else:
                return super_fwd(*args, **kwargs)

    return Checkpointed

class Unet(nn.Module):
    def __init__(self, in_features=1, up=None, down=None,
                 size=5, setup=fat_setup):

        super(Unet, self).__init__()

        if not len(down) == len(up) + 1:
            raise ValueError("`down` must be 1 item longer than `up`")

        self.up = up
        self.down = down
        self.in_features = in_features

        DownBlock = setup['down_block']
        UpBlock = setup['up_block']

        if 'checkpointed' in setup and setup['checkpointed']:
            UpBlock = checkpointed(UpBlock)
            DownBlock = checkpointed(DownBlock)

        down_dims = [in_features] + down
        self.path_down = nn.ModuleList()
        for i, (d_in, d_out) in enumerate(zip(down_dims[:-1], down_dims[1:])):
            block = DownBlock(
                d_in, d_out, size=size, name=f'down_{i}', setup=setup, is_first=i==0,
            )
            self.path_down.append(block)

        bot_dims = [down[-1]] + up
        hor_dims = down_dims[-2::-1]
        self.path_up = nn.ModuleList()
        for i, (d_bot, d_hor, d_out) in enumerate(zip(bot_dims, hor_dims, up)):
            block = UpBlock(
                d_bot, d_hor, d_out, size=size, name=f'up_{i}', setup=setup
            )
            self.path_up.append(block)

        self.n_params = 0
        for param in self.parameters():
            self.n_params += param.numel()


    @dimchecked
    def forward(self, inp: ['b', 'fi', 'hi', 'wi']) -> ['b', 'fo', 'ho', 'wo']:
        if inp.size(1) != self.in_features:
            fmt = "Expected {} feature channels in input, got {}"
            msg = fmt.format(self.in_features, inp.size(1))
            raise ValueError(msg)

        features = [inp]
        for i, layer in enumerate(self.path_down):
            features.append(layer(features[-1]))

        f_bot = features[-1]
        features_horizontal = features[-2::-1]

        for layer, f_hor in zip(self.path_up, features_horizontal):
            f_bot = layer(f_bot, f_hor)

        return f_bot
