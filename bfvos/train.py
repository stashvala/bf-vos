import os
import numpy as np
import torch
import torch.optim as optim
from torch.utils.data import DataLoader

from .dataset import davis
from .model import network, loss, config
import logging
import time

# Logging setup
logging.basicConfig()
logger = logging.getLogger()

# Set paths
root_dir = os.path.dirname(__file__)
model_dir = os.path.join(root_dir, 'model')
checkpoint_dir = os.path.join(model_dir)
config_save_dir = os.path.join(model_dir)

# Intervals
log_interval = 10
checkpoint_interval = 10

# Pytorch configs
device = "cpu"
# device = "cuda"
torch.set_default_tensor_type(config.DEFAULT_TENSOR_TYPE)
torch.set_default_dtype(config.DEFAULT_DTYPE)
torch.device(device)
seed = None
if seed is not None:
    np.random.seed(seed)
    torch.manual_seed(seed)

# Model configuration
image_width = 50
image_height = 50
embedding_vector_dims = 128

# Training parameters
# batch_size = 1
num_epochs = 1
learning_rate = 1e-3
momentum = 0.1
use_cuda = False
num_anchor_sample_points = 256  # according to paper
alpha = 1  # slack variable for loss

def main():
    data_source = davis.DavisDataset(base_dir=os.path.join(root_dir, 'dataset', 'DAVIS'),
                                     image_size=(image_width, image_height), year=2016, phase='train',
                                     transform=davis.ToTensor())
    triplet_sampler = davis.TripletSampler(dataset=data_source, randomize=True)
    data_loader = DataLoader(dataset=data_source, batch_sampler=triplet_sampler)

    model = network.BFVOSNet(embedding_vector_dims=embedding_vector_dims)
    loss_fn = loss.MinTripletLoss(alpha=alpha)
    optimizer = optim.SGD(model.parameters(), lr=learning_rate, momentum=momentum)

    for epoch in range(num_epochs):
        logger.info("Epoch {}/{}".format(epoch + 1, num_epochs))
        train(data_loader, model, loss_fn, optimizer, epoch)

    # Save final model
    model.eval().cpu()
    save_model_filename = "epoch_{}_{}.model".format(num_epochs, str(time.time()).replace(" ", "_"))
    save_model_path = os.path.join(model_dir, save_model_filename)
    torch.save(model.state_dict(), save_model_path)
    logger.info("Model saved to {}".format(save_model_filename))


def train(data_loader, model, loss_fn, optimizer, epoch):
    agg_fg_loss = 0.
    agg_bg_loss = 0.
    for idx, sample in enumerate(data_loader):
        sample_frames = torch.autograd.Variable(sample['image'])
        embeddings = model(sample_frames)

        embedding_a = embeddings[0]
        embedding_f1 = embeddings[1]
        embedding_f2 = embeddings[2]
        embedding_pool_points = torch.cat([embedding_f1, embedding_f2], 2)  # horizontally stacked frame1 and frame 2
        # embedding_a/p/n is of shape (128, w/8, h/8)

        # TODO: Randomly sample anchor points from anchor frame
        # For now, use all anchor points in the image
        anchor_points = torch.ByteTensor(sample['annotation'][0])  # all anchor points
        fg_anchor_indices = torch.nonzero(anchor_points)
        bg_anchor_indices = torch.nonzero(anchor_points == 0)

        # all_pool_points is a binary tensor of shape (w/8, h/8).
        # For any index in all_pool_points, if it 1 => it is a foreground pixel
        all_pool_points = torch.cat([sample['annotation'][1], sample['annotation'][2]], 1)
        fg_pool_indices = torch.nonzero(all_pool_points)
        bg_pool_indices = torch.nonzero(all_pool_points == 0)

        fg_embedding_a = torch.cat([embedding_a[:, x, y].unsqueeze(0) for x, y in fg_anchor_indices])
        bg_embedding_a = torch.cat([embedding_a[:, x, y].unsqueeze(0) for x, y in bg_anchor_indices])

        # Compute loss for all foreground anchor points
        # For foreground anchor points,
        # positive pool: all foreground points in all_pool_points
        # negative pool: all background points in all_pool_points
        fg_positive_pool = torch.cat([embedding_pool_points[:, x, y].unsqueeze(0) for x, y in fg_pool_indices])
        bg_positive_pool = torch.cat([embedding_pool_points[:, x, y].unsqueeze(0) for x, y in bg_pool_indices])

        fg_negative_pool = bg_positive_pool
        bg_negative_pool = fg_positive_pool

        fg_loss = loss_fn(fg_embedding_a, fg_positive_pool, fg_negative_pool)
        bg_loss = loss_fn(bg_embedding_a, bg_positive_pool, bg_negative_pool)
        final_loss = fg_loss + bg_loss

        # Backpropagation
        optimizer.zero_grad()
        final_loss.backward()
        optimizer.step()

        # Logging

        agg_fg_loss += fg_loss.item()
        agg_bg_loss += bg_loss.item()
        if (idx + 1) % log_interval == 0:
            logger.info("Epoch: {}, Batch: {}".format(epoch + 1, idx + 1))
            logger.info("Avg FG Loss: {}, Avg BG Loss: {}, Avg Total Loss: {}".format(agg_fg_loss / (idx + 1),
                                                                                      agg_bg_loss / (idx + 1),
                                                                                      (agg_fg_loss + agg_bg_loss) / (
                                                                                          idx + 1)))

        if (idx + 1) % checkpoint_interval == 0:
            model.eval().cpu()
            ckpt_filename = "ckpt_epoch_{}_batch_id_{}.pth".format(epoch + 1, idx + 1)
            ckpt_path = os.path.join(checkpoint_dir, ckpt_filename)
            torch.save(model.state_dict(), ckpt_path)
            logger.info("Checkpoint saved at {}".format(ckpt_filename))
            model.to(device).train()


if __name__ == "__main__":
    main()
