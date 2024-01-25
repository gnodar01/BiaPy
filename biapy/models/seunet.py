import torch
import torch.nn as nn

from biapy.models.blocks import DoubleConvBlock, UpBlock

class SE_U_Net(nn.Module):
    """
    Create 2D/3D U-Net with squeeze-excite blocks. 
    
    Reference: `Squeeze and Excitation Networks <https://arxiv.org/abs/1709.01507>`_.

    Parameters
    ----------
    image_shape : 3D/4D tuple
        Dimensions of the input image. E.g. ``(y, x, channels)`` or ``(z, y, x, channels)``.

    activation : str, optional
        Activation layer.

    feature_maps : array of ints, optional
        Feature maps to use on each level.

    drop_values : float, optional
        Dropout value to be fixed.

    batch_norm : bool, optional
        Make batch normalization.

    k_size : int, optional
        Kernel size.

    upsample_layer : str, optional
        Type of layer to use to make upsampling. Two options: "convtranspose" or "upsampling". 

    z_down : List of ints, optional
        Downsampling used in z dimension. Set it to ``1`` if the dataset is not isotropic.

    n_classes: int, optional
        Number of classes.

    output_channels : str, optional
        Channels to operate with. Possible values: ``BC``, ``BCD``, ``BP``, ``BCDv2``,
        ``BDv2``, ``Dv2`` and ``BCM``.

    upsampling_factor : int, optional
        Factor of upsampling for super resolution workflow. 

    upsampling_position : str, optional
        Whether the upsampling is going to be made previously (``pre`` option) to the model 
        or after the model (``post`` option).

    Returns
    -------
    model : Torch model
        U-Net model.

    Calling this function with its default parameters returns the following network:

    .. image:: ../../img/models/unet.png
        :width: 100%
        :align: center

    Image created with `PlotNeuralNet <https://github.com/HarisIqbal88/PlotNeuralNet>`_.
    """
    def __init__(self, image_shape=(256, 256, 1), activation="ELU", feature_maps=[32, 64, 128, 256], drop_values=[0.1,0.1,0.1,0.1],
        batch_norm=False, k_size=3, upsample_layer="convtranspose", z_down=[2,2,2,2], n_classes=1, 
        output_channels="BC", upsampling_factor=1, upsampling_position="pre"):
        super(SE_U_Net, self).__init__()

        self.depth = len(feature_maps)-1
        self.ndim = 3 if len(image_shape) == 4 else 2 
        self.z_down = z_down
        self.n_classes = 1 if n_classes <= 2 else n_classes
        if self.ndim == 3:
            conv = nn.Conv3d
            convtranspose = nn.ConvTranspose3d
            batchnorm_layer = nn.BatchNorm3d if batch_norm else None
            self.pooling = nn.MaxPool3d
        else:
            conv = nn.Conv2d
            convtranspose = nn.ConvTranspose2d
            batchnorm_layer = nn.BatchNorm2d if batch_norm else None
            self.pooling = nn.MaxPool2d
            
        # Super-resolution
        self.pre_upsampling = None
        if upsampling_factor > 1 and upsampling_position == "pre":
            mpool = (1, 2, 2) if self.ndim == 3 else (2, 2)
            self.pre_upsampling = convtranspose(image_shape[-1], image_shape[-1], kernel_size=mpool, stride=mpool)

        # ENCODER
        self.down_path = nn.ModuleList()
        in_channels = image_shape[-1]
        for i in range(self.depth):
            self.down_path.append( 
                DoubleConvBlock(conv, in_channels, feature_maps[i], k_size, activation, batchnorm_layer,
                    drop_values[i], se_block=True)
            )
            in_channels = feature_maps[i]

        self.bottleneck = DoubleConvBlock(conv, in_channels, feature_maps[-1], k_size, activation, batchnorm_layer,
            drop_values[-1])

        # DECODER
        self.up_path = nn.ModuleList()
        in_channels = feature_maps[-1]
        for i in range(self.depth-1, -1, -1):
            self.up_path.append( 
                UpBlock(self.ndim, convtranspose, in_channels, feature_maps[i], z_down[i], upsample_layer, 
                    conv, k_size, activation, batchnorm_layer, drop_values[i], se_block=True)
            )
            in_channels = feature_maps[i]
        
        # Super-resolution
        self.post_upsampling = None
        if upsampling_factor > 1 and upsampling_position == "post":
            mpool = (1, 2, 2) if self.ndim == 3 else (2, 2)
            self.post_upsampling = convtranspose(feature_maps[0], self.n_classes, kernel_size=mpool, stride=mpool)

        # Instance segmentation
        if output_channels is not None:
            if output_channels == "Dv2":
                self.last_block = conv(feature_maps[0], 1, kernel_size=1, padding='same')
            elif output_channels in ["BC", "BP"]:
                self.last_block = conv(feature_maps[0], 2, kernel_size=1, padding='same')
            elif output_channels in ["BDv2", "BD"]:
                self.last_block = conv(feature_maps[0], 2, kernel_size=1, padding='same')
            elif output_channels in ["BCM", "BCD", "BCDv2"]:
                self.last_block = conv(feature_maps[0], 3, kernel_size=1, padding='same')
        # Other
        else:
            self.last_block = conv(feature_maps[0], self.n_classes, kernel_size=1, padding='same')

        self.apply(self._init_weights)

    def forward(self, x):
        # Super-resolution
        if self.pre_upsampling is not None:
            x = self.pre_upsampling(x)

        # Down
        blocks = []
        for i, down in enumerate(self.down_path):
            x = down(x)
            if i != len(self.down_path):
                blocks.append(x)
                mpool = (self.z_down[i], 2, 2) if self.ndim == 3 else (2, 2)
                x = self.pooling(mpool)(x) 

        x = self.bottleneck(x) 

        # Up
        for i, up in enumerate(self.up_path):
            x = up(x, blocks[-i - 1])

        # Super-resolution
        if self.post_upsampling is not None:
            x = self.post_upsampling(x)
            
        x = self.last_block(x)

        # Clip values in SR
        if self.pre_upsampling is not None or self.post_upsampling is not None:
            x = torch.clamp(x, min=0, max=1)  
            
        return x

    def _init_weights(self, m):
        if isinstance(m, nn.Conv2d) or isinstance(m, nn.Conv3d):
            nn.init.xavier_uniform_(m.weight)
            if m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, nn.Linear):
            nn.init.xavier_uniform_(m.weight)
            if isinstance(m, nn.Linear) and m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, nn.LayerNorm):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)

