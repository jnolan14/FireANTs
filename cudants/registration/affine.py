from cudants.registration.abstract import AbstractRegistration
from typing import List, Optional
import torch
from torch import nn
from cudants.io.image import BatchedImages
from torch.optim import SGD, Adam
from torch.nn import functional as F
from cudants.utils.globals import MIN_IMG_SIZE
from tqdm import tqdm
import numpy as np

class AffineRegistration(AbstractRegistration):

    def __init__(self, scales: List[int], iterations: List[float], 
                fixed_images: BatchedImages, moving_images: BatchedImages,
                loss_type: str = "cc",
                optimizer: str = 'SGD', optimizer_params: dict = {},
                optimizer_lr: float = 0.1, optimizer_momentum: float = 0.0,
                mi_kernel_type: str = 'b-spline', cc_kernel_type: str = 'rectangular',
                tolerance: float = 1e-6, max_tolerance_iters: int = 10, tolerance_mode: str = 'atol',
                init_rigid: Optional[torch.Tensor] = None,
                custom_loss: nn.Module = None) -> None:
        super().__init__(scales, iterations, fixed_images, moving_images, loss_type, mi_kernel_type, cc_kernel_type, custom_loss,
                         tolerance, max_tolerance_iters, tolerance_mode)
        # initialize transform
        device = fixed_images.device
        self.dims = dims = self.moving_images.dims
        # first three params are so(n) variables, last three are translation
        if init_rigid is not None:
            affine = init_rigid
        else:
            affine = torch.eye(dims, dims+1).unsqueeze(0).repeat(fixed_images.size(), 1, 1)  # [N, D]
        self.affine = nn.Parameter(affine.to(device))  # [N, D]
        self.row = torch.zeros((fixed_images.size(), 1, dims+1)).to(device)   # keep this to append to affine matrix
        self.row[:, 0, -1] = 1.0
        # optimizer
        if optimizer == 'SGD':
            self.optimizer = SGD([self.affine], lr=optimizer_lr, momentum=optimizer_momentum, **optimizer_params)
        elif optimizer == 'Adam':
            self.optimizer = Adam([self.affine], lr=optimizer_lr, **optimizer_params)
        else:
            raise ValueError(f"Optimizer {optimizer} not supported")
    
    def get_affine_matrix(self):
        return torch.cat([self.affine, self.row], dim=1)

    def optimize(self, save_transformed=False):
        ''' Given fixed and moving images, optimize rigid registration '''
        fixed_arrays = self.fixed_images()
        moving_arrays = self.moving_images()
        fixed_t2p = self.fixed_images.get_torch2phy()
        moving_p2t = self.moving_images.get_phy2torch()
        fixed_size = fixed_arrays.shape[2:]
        # save initial affine transform to initialize grid 
        init_grid = torch.eye(self.dims, self.dims+1).to(self.fixed_images.device).unsqueeze(0).repeat(self.fixed_images.size(), 1, 1)  # [N, dims, dims+1]

        if save_transformed:
            transformed_images = []

        for scale, iters in zip(self.scales, self.iterations):
            tol_ctr = 0
            prev_loss = np.inf
            # downsample fixed array and retrieve coords
            size_down = [max(int(s / scale), MIN_IMG_SIZE) for s in fixed_size]
            fixed_image_down = F.interpolate(fixed_arrays, size=size_down, mode=self.fixed_images.interpolate_mode, align_corners=True)
            fixed_image_coords = F.affine_grid(init_grid, fixed_image_down.shape, align_corners=True)  # [N, H, W, [D], dims+1]
            fixed_image_coords_homo = torch.cat([fixed_image_coords, torch.ones(list(fixed_image_coords.shape[:-1]) + [1], device=fixed_image_coords.device)], dim=-1)
            fixed_image_coords_homo = torch.einsum('ntd, n...d->n...t', fixed_t2p, fixed_image_coords_homo)  # [N, H, W, [D], dims+1]  
            # print(fixed_image_down.min(), fixed_image_down.max())
            # this is in physical space
            pbar = tqdm(range(iters))
            for i in pbar:
                self.optimizer.zero_grad()
                affinemat = self.get_affine_matrix()
                coords = torch.einsum('ntd, n...d->n...t', affinemat, fixed_image_coords_homo)  # [N, H, W, [D], dims+1]
                coords = torch.einsum('ntd, n...d->n...t', moving_p2t, coords)  # [N, H, W, [D], dims+1]
                # print(moving_p2t, fixed_t2p)
                # print(coords[..., :-1].reshape(-1, self.dims).min(0).values, coords[..., :-1].reshape(-1, self.dims).max(0).values)
                # input()
                # sample from these coords
                moved_image = F.grid_sample(moving_arrays, coords[..., :-1], mode='bilinear', align_corners=True)  # [N, C, H, W, [D]]
                loss = self.loss_fn(moved_image, fixed_image_down) 
                loss.backward()
                # print(self.transl.grad, self.rotation.grad)
                self.optimizer.step()
                # check for convergence
                cur_loss = loss.item()
                if self.tolerance_mode == 'atol':
                    if (prev_loss - cur_loss) < self.tolerance:
                        tol_ctr+=1
                        if tol_ctr > self.max_tolerance_iters:
                            break
                    else:
                        tol_ctr=0
                elif self.tolerance_mode == 'rtol':
                    if (prev_loss - cur_loss)/(prev_loss) < self.tolerance:
                        tol_ctr+=1
                        if tol_ctr > self.max_tolerance_iters:
                            break
                    else:
                        tol_ctr=0
                prev_loss = cur_loss
                pbar.set_description("scale: {}, iter: {}/{}, loss: {:4f}".format(scale, i, iters, prev_loss))
            if save_transformed:
                transformed_images.append(moved_image)

        if save_transformed:
            return transformed_images


if __name__ == '__main__':
    from cudants.io.image import Image, BatchedImages
    img1 = Image.load_file('/data/BRATS2021/training/BraTS2021_00598/BraTS2021_00598_t1.nii.gz')
    img2 = Image.load_file('/data/BRATS2021/training/BraTS2021_00599/BraTS2021_00599_t1.nii.gz')
    fixed = BatchedImages([img1, ])
    moving = BatchedImages([img2,])
    transform = AffineRegistration([8, 4, 2, 1], [1000, 500, 250, 100], fixed, moving, loss_type='cc', optimizer='SGD', optimizer_lr=1e-1, tolerance=0)
    transform.optimize()
    print(np.around(transform.affine.data.cpu().numpy(), 4))
