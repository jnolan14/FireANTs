import torch
import torch.nn.functional as F
from cudants.utils.util import catchtime
from typing import Optional, List
from cudants.losses.cc import gaussian_1d, separable_filtering
from cudants.types import ItemOrList

def downsample(image: ItemOrList[torch.Tensor], size: List[int], mode: str, sigma: Optional[torch.Tensor]=None,
               gaussians: Optional[torch.Tensor] = None) -> torch.Tensor:
    ''' 
    this function is to downsample the image to the given size
    but first, we need to perform smoothing 
    if sigma is provided (in voxels), then use this sigma for downsampling, otherwise infer sigma
    '''
    if gaussians is None:
        if sigma is None:
            orig_size = list(image.shape[2:])
            sigma = [0.5 * orig_size[i] / size[i] for i in range(len(orig_size))]   # use sigma as the downsampling factor
        sigma = torch.tensor(sigma, dtype=torch.float32, device=image.device)
        # create gaussian convs
        gaussians = [gaussian_1d(s, truncated=2) for s in sigma]
    # otherwise gaussians is given, just downsample
    image_smooth = separable_filtering(image, gaussians)
    image_down = F.interpolate(image_smooth, size=size, mode=mode, align_corners=True)
    return image_down

def apply_gaussian(image: torch.Tensor, sigma: torch.Tensor, truncated: float = 2) -> torch.Tensor:
    '''
    wrap the `gaussian_1d` and `separable filtering into one`
    '''
    gaussians = [gaussian_1d(s, truncated=truncated) for s in sigma]
    image_smooth = separable_filtering(image, gaussians)
    return image_smooth


def scaling_and_squaring(u, grid, n = 6):
    """
    Apply scaling and squaring to a displacement field
    
    :param u: Input stationary velocity field, PyTorch tensor of shape [B, D, H, W, 3] or [B, H, W, 2]
    :param grid: Sampling grid of size [B, D, H, W, dims]  or [B, H, W, dims]
    :param n: Number of iterations of scaling and squaring (default: 6)
    
    :returns: Output displacement field, v, PyTorch tensor of shape [B, D, H, W, dims] or [B, H, W, dims]
    """
    dims = u.shape[-1]
    if dims == 3:
        v = (1.0/2**n) * u
        for i in range(n):
            vimg = v.permute(0, 4, 1, 2, 3)          # [1, 3, D, H, W]
            v = v + F.grid_sample(vimg, v + grid, align_corners=True).permute(0, 2, 3, 4, 1)
    elif dims == 2:
        v = (1.0/2**n) * u
        for i in range(n):
            vimg = v.permute(0, 3, 1, 2)
            v = v + F.grid_sample(vimg, v + grid, align_corners=True).permute(0, 2, 3, 1)
    else:
        raise ValueError('Invalid dimension: {}'.format(dims))
    return v

def image_gradient_singlechannel(image):
    """
    Compute the gradient of an image using central difference approximation
    :param I: input image, represented as a [B,1,D,H,W] or [B,1,H,W] tensor
    :returns: gradient of the input image, represented as a [B,C,D,H,W] or [B,C,H,W]  tensor

    :TODO: Add support for multichannel images
    """
    dims = len(image.shape) - 2
    device = image.device
    grad = None
    if dims == 2:
        B, C, H, W = image.shape
        k = torch.cuda.FloatTensor([[-1.0, 0, 1]], device=device)[None, None]
        gradx = F.conv2d(image, k, padding=(0, 1))
        grady = F.conv2d(image, k.permute(0, 1, 3, 2), padding=(1, 0))
        grad = torch.cat([gradx, grady], dim=1)
    elif dims == 3:
        B, C, D, H, W = image.shape
        k = torch.cuda.FloatTensor([[[-1.0, 0, 1]]], device=device)[None, None]
        gradx = F.conv3d(image, k, padding=(0, 0, 1))
        grady = F.conv3d(image, k.permute(0, 1, 2, 4, 3), padding=(0, 1, 0))
        gradz = F.conv3d(image, k.permute(0, 1, 4, 2, 3), padding=(1, 0, 0))
        grad = torch.cat([gradx, grady, gradz], dim=1)
    else:
        raise ValueError('Invalid dimension: {}'.format(dims))
    return grad


def image_gradient(image):
    ''' compute the image gradient using central difference approximation '''
    c = image.shape[1]
    if c == 1:
        return image_gradient_singlechannel(image)
    else:
        raise NotImplementedError('Multichannel images not supported yet')

# no_grad versions
scaling_and_squaring_nograd = torch.no_grad()(scaling_and_squaring)
image_gradient_nograd = torch.no_grad()(image_gradient)

if __name__ == '__main__':
    pass
    ### Testing image gradient
    # from timeit import timeit
    # image = torch.rand(1, 1, 128, 128, 128).cuda()
    # timegrad = timeit(lambda: image_gradient(image), number=1000)/1000
    # timenograd = timeit(lambda: image_gradient_nograd(image), number=1000)/1000
    # print("Time (no_grad): {:.5f} s".format(timenograd))
    # print("Time (grad): {:.5f} s".format(timegrad))
    # print("Speedup: {:.2f}x".format(timegrad/timenograd))

    ### Not a massive speedup from no_grad in scaling and squaring (around 1.06x)
    # from timeit import timeit
    # H = 128
    # image = torch.rand(1, 1, H, H, H).cuda()
    # affine = torch.eye(3, 4).unsqueeze(0).cuda().requires_grad_(True)
    # grid = F.affine_grid(affine, image.size(), align_corners=True)
    # u = torch.rand(1, H, H, H, 3).cuda()
    # N = 1000
    # timenograd = timeit(lambda: scaling_and_squaring_nograd(u, grid, n=6), number=N)/N
    # timegrad = timeit(lambda: scaling_and_squaring(u, grid, n=6), number=N)/N
    # print("Time (no_grad): {:.5f} s".format(timenograd))
    # print("Time (grad): {:.5f} s".format(timegrad))
    # print("Speedup: {:.2f}x".format(timegrad/timenograd))