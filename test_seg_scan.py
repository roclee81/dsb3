import sys
import lasagne as nn
import numpy as np
import theano
import pathfinder
import utils
from configuration import config, set_configuration
from utils_plots import plot_slice_3d_3
import theano.tensor as T
import utils_lung
import blobs_detection
import logger

theano.config.warn_float64 = 'raise'

if len(sys.argv) < 2:
    sys.exit("Usage: test_luna_scan.py <configuration_name>")

config_name = sys.argv[1]
set_configuration('configs_seg_scan', config_name)

# predictions path
predictions_dir = utils.get_dir_path('model-predictions', pathfinder.METADATA_PATH)
outputs_path = predictions_dir + '/%s' % config_name
utils.auto_make_dir(outputs_path)

# logs
logs_dir = utils.get_dir_path('logs', pathfinder.METADATA_PATH)
sys.stdout = logger.Logger(logs_dir + '/%s.log' % config_name)
sys.stderr = sys.stdout

# builds model and sets its parameters
model = config().build_model()

x_shared = nn.utils.shared_empty(dim=len(model.l_in.shape))
idx_z = T.lscalar('idx_z')
idx_y = T.lscalar('idx_y')
idx_x = T.lscalar('idx_x')

fs = config().filter_size
stride = config().stride
n_windows = (config().p_transform['patch_size'][0] - fs) / stride + 1

givens_valid = {}
givens_valid[model.l_in.input_var] = x_shared[:, :,
                                     idx_z * stride:(idx_z * stride) + fs,
                                     idx_y * stride:(idx_y * stride) + fs,
                                     idx_x * stride:(idx_x * stride) + fs]

get_predictions_patch = theano.function([idx_z, idx_y, idx_x],
                                        nn.layers.get_output(model.l_out, deterministic=True),
                                        givens=givens_valid,
                                        on_unused_input='ignore')

valid_data_iterator = config().valid_data_iterator

print
print 'Data'
print 'n validation: %d' % valid_data_iterator.nsamples

valid_losses_dice = []
n_pos = 0
tp = 0
n_blobs = 0
pid2blobs = {}
for n, (x, y, id, annotations, transform_matrices) in enumerate(valid_data_iterator.generate()):
    pid = id[0]
    print '-------------------------------------'
    print n, pid
    annotations = annotations[0]
    tf_matrix = transform_matrices[0]

    predictions_scan = np.zeros((1, 1, n_windows * stride, n_windows * stride, n_windows * stride))

    x_shared.set_value(x)
    for iz in xrange(n_windows):
        for iy in xrange(n_windows):
            for ix in xrange(n_windows):
                predictions_patch = get_predictions_patch(iz, iy, ix)
                predictions_scan[0, 0,
                iz * stride:(iz + 1) * stride,
                iy * stride:(iy + 1) * stride,
                ix * stride:(ix + 1) * stride] = predictions_patch[0, 0,
                                                 stride / 2:stride * 3 / 2,
                                                 stride / 2:stride * 3 / 2,
                                                 stride / 2:stride * 3 / 2, ] \
                    if config().extract_middle else predictions_patch[0, 0, :, :, :]

    predictions_scan = np.clip(predictions_scan, 0, 1)

    if predictions_scan.shape != y.shape:
        pad_width = (np.asarray(y.shape) - np.asarray(predictions_scan.shape)) / 2
        pad_width = [(p, p) for p in pad_width]
        predictions_scan = np.pad(predictions_scan, pad_width=pad_width, mode='constant')

    d = utils_lung.dice_index(predictions_scan, y)
    print '\n dice index: ', d
    valid_losses_dice.append(d)

    for nodule_n, zyxd in enumerate(annotations):
        plot_slice_3d_3(input=x[0, 0], mask=y[0, 0], prediction=predictions_scan[0, 0],
                        axis=0, pid='-'.join([str(n), str(nodule_n), str(id[0])]),
                        img_dir=outputs_path, idx=zyxd)
    print 'saved plot'

    print 'computing blobs'
    blobs = blobs_detection.blob_dog(predictions_scan[0, 0], min_sigma=1, max_sigma=15, threshold=0.1)

    n_blobs += len(blobs)
    for zyxd in annotations:
        n_pos += 1
        r = zyxd[-1] / 2.
        distance2 = ((zyxd[0] - blobs[:, 0]) ** 2
                     + (zyxd[1] - blobs[:, 1]) ** 2
                     + (zyxd[2] - blobs[:, 2]) ** 2)
        blob_idx = np.argmin(distance2)
        blob = blobs[blob_idx]
        print 'node', zyxd
        print 'closest blob', blob
        if distance2[blob_idx] < r ** 2:
            tp += 1
        else:
            print 'not detected !!!'
    print 'n blobs/ detected', n_pos, tp

    # TODO mark correct blobs with 1 and wrong with 0
    # we will save blobs the the voxel space of the original image
    blobs_original_voxel_coords = []
    for j in xrange(blobs.shape[0]):
        blob_j = np.append(blobs[j, :3], [1])
        blobs_original_voxel_coords.append(tf_matrix.dot(blob_j))
    pid2blobs[pid] = np.asarray(blobs_original_voxel_coords)
    utils.save_pkl(pid2blobs, outputs_path + '/candidates.pkl')

print 'Dice index validation loss', np.mean(valid_losses_dice)
print 'n nodules', n_pos
print 'n TP', tp
print 'n blobs', n_blobs
