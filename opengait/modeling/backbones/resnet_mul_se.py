import torch
from torch.nn import functional as F
import torch.nn as nn
from torchvision.models.resnet import BasicBlock, Bottleneck, ResNet
from ..modules import BasicConv2d

block_map = {'BasicBlock': BasicBlock,
             'Bottleneck': Bottleneck}

class SEBlock(nn.Module):
    def __init__(self, channels, reduction=16):
        super(SEBlock, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
            nn.Linear(channels, channels // reduction),
            nn.ReLU(inplace=True),
            nn.Linear(channels // reduction, channels),
            nn.Sigmoid()
        )

    def forward(self, x):
        b, c, _, _ = x.size()
        y = self.avg_pool(x).view(b, c)
        y = self.fc(y).view(b, c, 1, 1)
        return x * y.expand_as(x)

class resnet_mul3_se(ResNet):
    def __init__(self, block, channels=[64, 128, 256, 512], in_channel=1, layers=[1, 2, 2, 1], strides=[1, 2, 2, 1],
                 maxpool=True):
        if block in block_map.keys():
            block = block_map[block]
        else:
            raise ValueError(
                "Error type for -block-Cfg-, supported: 'BasicBlock' or 'Bottleneck'.")
        self.maxpool_flag = maxpool
        super(resnet_mul3_se, self).__init__(block, layers)

        # Not used #
        self.fc = None
        ############
        self.inplanes = channels[0]
        self.bn1 = nn.BatchNorm2d(self.inplanes)

        self.conv1 = BasicConv2d(in_channel, self.inplanes, 3, 1, 1)

        self.layer1 = self._make_layer(
            block, channels[0], layers[0], stride=strides[0], dilate=False)

        self.layer2 = self._make_layer(
            block, channels[1], layers[1], stride=strides[1], dilate=False)
        self.layer3 = self._make_layer(
            block, channels[2], layers[2], stride=strides[2], dilate=False)
        self.layer4 = self._make_layer(
            block, channels[3], layers[3], stride=strides[3], dilate=False)

        #SE Blocks
        #self.se = SEBlock(512 * block.expansion)
        self.se1 = SEBlock(channels[0] * block.expansion)
        self.se2 = SEBlock(channels[1] * block.expansion)
        self.se3 = SEBlock(channels[2] * block.expansion)
        self.se4 = SEBlock(channels[3] * block.expansion)

        self.pyramid_conv1 = nn.Conv2d(channels[0], 512, kernel_size=1, stride=1, bias=False).cuda()
        self.pyramid_conv2 = nn.Conv2d(channels[1], 512, kernel_size=1, stride=1, bias=False).cuda()
        self.pyramid_conv3 = nn.Conv2d(channels[2], 512, kernel_size=1, stride=1, bias=False).cuda()
        self.pyramid_conv4 = nn.Conv2d(channels[3], 512, kernel_size=1, stride=1, bias=False).cuda()

    def _make_layer(self, block, planes, blocks, stride=1, dilate=False):
        if blocks >= 1:
            layer = super()._make_layer(block, planes, blocks, stride=stride, dilate=dilate)
        else:
            def layer(x): return x
        return layer

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = F.relu(x)
        if self.maxpool_flag:
            x = F.max_pool2d(x, kernel_size=3, stride=2, padding=1)

        c1 = self.layer1(x)
        c1 = self.se1(c1)
        #print("c1:",c1.shape)
        c2 = self.layer2(c1)
        c2 = self.se2(c2)
        #print("c2:", c2.shape)
        c3 = self.layer3(c2)
        c3 = self.se3(c3)
        #print("c3:", c3.shape)
        c4 = self.layer4(c3)
        c4 = self.se4(c4)
        #print("c4:", c4.shape)

        # 构建特征金字塔
        p4 = self.pyramid_conv4(c4)
        #print("p4:", p4.shape)
        p3 = self.pyramid_conv3(c3)
        p2 = self.pyramid_conv2(c2)
        p1 = self.pyramid_conv1(c1)

        # 降采样
        p2 = F.interpolate(p2, scale_factor=0.5, mode='nearest')
        # print('p2:', p2.shape)
        p1 = F.interpolate(p1, scale_factor=0.5, mode='nearest')
        p1 = F.interpolate(p1, scale_factor=0.5, mode='nearest')

        # 融合所有尺度的特征
        fused_feature = p1 + p2 + p3 + p4
        #print('fused_feature:', fused_feature.shape)
        c4 = fused_feature

        return c4