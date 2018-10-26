import torch
import numpy as np

class DilatedCausalConv1d(torch.nn.Module):
    def __init__(self, in_channels, out_channels, dilation):
        super(DilatedCausalConv1d, self).__init__()
        self.padding = dilation
        self.conv = torch.nn.Conv1d(in_channels, out_channels, 
                                    kernel_size=2, stride=1, 
                                    padding=self.padding, 
                                    dilation=dilation, bias=False)

    def forward(self, input):
        output = self.conv(input)
        return output[:, :, :-self.padding]

class ResidualBlock(torch.nn.Module):
    def __init__(self, residual_channels, dilation_channels, skip_channels, dilation):
        super(ResidualBlock, self).__init__()
        self.dilated_conv = DilatedCausalConv1d(residual_channels, residual_channels, dilation=dilation)
        self.tanh_conv = torch.nn.Conv1d(residual_channels, dilation_channels, 1)
        self.sigmoid_conv = torch.nn.Conv1d(residual_channels, dilation_channels, 1)
        self.residual_conv = torch.nn.Conv1d(dilation_channels, residual_channels, 1)
        self.skip_conv = torch.nn.Conv1d(dilation_channels, skip_channels, 1)
        self.gate_tanh = torch.nn.Tanh()
        self.gate_sigmoid = torch.nn.Sigmoid()

    def forward(self, input, skip_size):
        output = self.dilated_conv(input)

        gated_tanh = self.tanh_conv(output)
        gated_sigmoid = self.sigmoid_conv(output)
        gated_tanh = self.gate_tanh(gated_tanh)
        gated_sigmoid = self.gate_sigmoid(gated_sigmoid)
        gated = gated_tanh * gated_sigmoid

        output = self.residual_conv(gated)
        output += input[:, :, -output.size()[2]:]

        skip = self.skip_conv(gated)
        #skip = skip[:, :, -skip_size:]
        return output, skip

class ResidualStack(torch.nn.Module):
    def __init__(
            self, 
            layer_size, 
            stack_size, 
            residual_channels, 
            dilation_channels, 
            skip_channels
        ):
        super(ResidualStack, self).__init__()
        self.layer_size = layer_size
        self.stack_size = stack_size
        self.res_blocks = torch.nn.ModuleList(
            self.stack_res_blocks(
                residual_channels, 
                dilation_channels, 
                skip_channels
            )
        )
        
    @staticmethod
    def _residual_block(residual_channels, dilation_channels, skip_channels, dilation):
        block = ResidualBlock(residual_channels, dilation_channels, skip_channels, dilation)
        return block

    def build_dilations(self):
        dilation = [2 ** i for i in range(self.layer_size)] * self.stack_size
        return dilation

    def stack_res_blocks(self, residual_channels, dilation_channels, skip_channels):
        dilations = self.build_dilations()
        res_blocks = [self._residual_block(residual_channels, dilation_channels, skip_channels, dilation) for dilation in dilations]
        return res_blocks
    
    def forward(self, input, skip_size):
        output = input
        sum = 0
        for res_block in self.res_blocks:
            output, skip = res_block(output, skip_size)
            sum += skip
        return sum

class PostProcess(torch.nn.Module):
    def __init__(self, skip_channels, end_channels, channels):
        super(PostProcess, self).__init__()
        self.conv1 = torch.nn.Conv1d(skip_channels, end_channels, 1)
        self.conv2 = torch.nn.Conv1d(end_channels, channels, 1)
        self.relu = torch.nn.ReLU()
        self.sigmoid = torch.nn.Sigmoid()

    def forward(self, input):
        output = self.relu(input)
        output = self.conv1(output)
        output = self.relu(output)
        output = self.conv2(output)
        output = self.sigmoid(output)
        return output

class Wavenet(torch.nn.Module):
    def __init__(
            self, 
            layer_size, 
            stack_size, 
            channels, 
            residual_channels, 
            dilation_channels, 
            skip_channels, 
            end_channels
        ):
        super(Wavenet, self).__init__()
        self.receptive_field = self.calc_receptive_field(layer_size, stack_size)
        self.causal = DilatedCausalConv1d(channels, residual_channels, 1)
        self.res_stacks = ResidualStack(
            layer_size, 
            stack_size, 
            residual_channels, 
            dilation_channels, 
            skip_channels
        )
        self.post = PostProcess(skip_channels, end_channels, channels)
    
    @staticmethod
    def calc_receptive_field(layer_size, stack_size):
        layers = [2 ** i for i in range(layer_size)] * stack_size
        num_receptive_fields = np.sum(layers)
        return int(num_receptive_fields)

    def calc_output_size(self, input):
        output_size = int(input.size()[2]) - self.receptive_field
        return output_size

    def forward(self, input):
        output_size = self.calc_output_size(input)
        output = self.causal(input)
        output = self.res_stacks(output, output_size)
        output = self.post(output)
        return output