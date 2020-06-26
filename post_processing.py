import numpy as np
from skimage import measure
import cv2
import os
from tqdm import tqdm
from scipy import ndimage as ndi
from skimage.morphology import disk
from skimage.segmentation import watershed
from skimage.filters import rank


def spuriuous_detection_filter(Y, low_score_th=0.6, th=0.45):
    """Based on the first post-processing method proposed in Oztel et al. where 
       removes the artifacts with low class score.
       
       Args:
            Y (4D Numpy array): data to apply the filter. 
            E.g. (img_number, x, y, channels). 
    
            low_score_th (float, optional): the minimun class score that the 
            artifact must have to not be discarded. Must be a vaue between 0 
            and 1. 
            
            th (float, optional): threshold applied to binarize the given images.
            Must be a vaue between 0 and 1.

       Return:
            class_Y (4D Numpy array): filtered data.
            E.g. (img_number, x, y, channels). 
    """
        
    if low_score_th < 0 or low_score_th > 1:
        raise ValueError("'low_score_th' must be a float between 0 and 1")
    if th < 0 or th > 1:
        raise ValueError("'th' must be a float between 0 and 1")

    class_Y = np.zeros(Y.shape[:3], dtype=np.uint8)
    class_Y[Y[...,0]>th] = 1 
    
    for i in range(class_Y.shape[0]):
        im = class_Y[i]
        im, num = measure.label(im, connectivity=2, background=0, return_num=True)
    
        for j in range(1,num):
            c_conf = np.mean(Y[i,...,0][im==j])
            if c_conf < low_score_th:
                print("Slice {}: removing artifact {} - pixels: {}"
                      .format(i, j, np.count_nonzero(Y[i,...,0][im==j])))
                class_Y[i][im==j] = 0

    return np.expand_dims(class_Y, -1)


def boundary_refinement_watershed(X, Y_pred, erode=True, save_marks_dir=None):
    """Apply watershed to the given predictions with the goal of refine the 
       boundaries of the artifacts.

       Args:
            X (4D Numpy array): original data to guide the watershed.
            E.g. (img_number, x, y, channels).

            Y_pred (4D Numpy array): predicted data to refine the boundaries.
            E.g. (img_number, x, y, channels).

            erode (bool, optional): flag to extract the sure foreground eroding 
            the artifacts instead of doing with distanceTransform.  

            save_marks_dir (str, optional): directory to save the markers used 
            to make the watershed. Useful for debugging. 

        Return:
            watershed_predictions (4D Numpy array): refined boundaries of the 
            predictions.  E.g. (img_number, x, y, channels).
    """

    if save_marks_dir is not None:
        os.makedirs(save_marks_dir, exist_ok=True)

    watershed_predictions = np.zeros(Y_pred.shape[:3])
    kernel = np.ones((3,3),np.uint8)
    d = len(str(X.shape[0]))

    for i in tqdm(range(X.shape[0])):
        im = cv2.cvtColor(X[i,...]*255, cv2.COLOR_GRAY2RGB)
        pred = Y_pred[i,...,0]
        
        # sure background area
        sure_bg = cv2.dilate(pred, kernel, iterations=3)
        sure_bg = np.uint8(sure_bg)
        
        # Finding sure foreground area
        if erode:
            sure_fg = cv2.erode(pred, kernel, iterations=3)
        else:
            dist_transform = cv2.distanceTransform(a, cv2.DIST_L2, 5)
            ret, sure_fg = cv2.threshold(
                dist_transform, 0.7*dist_transform.max(), 255,0)
        sure_fg = np.uint8(sure_fg)
        
        # Finding unknown region
        unknown_reg = cv2.subtract(sure_bg, sure_fg)
        
        # Marker labelling
        ret, markers = cv2.connectedComponents(sure_fg)
        
        # Add one to all labels so that sure background is not 0, but 1
        markers = markers+1
        
        # Now, mark the region of unknown with zero
        markers[unknown_reg==1] = 0
       
        if save_marks_dir is not None:
            f = os.path.join(save_marks_dir, "mark_" + str(i).zfill(d) + ".png")
            cv2.imwrite(f, markers)
       
        markers = cv2.watershed((im).astype(np.uint8), markers)
        
        watershed_predictions[i] = markers

    # Label all artifacts into 1 and the background with 0
    watershed_predictions[watershed_predictions==1] = 0
    watershed_predictions[watershed_predictions>1] = 1
    watershed_predictions[watershed_predictions==-1] = 0

    return np.expand_dims(watershed_predictions, -1)


def boundary_refinement_watershed2(X, Y_pred, save_marks_dir=None):
    """Apply watershed to the given predictions with the goal of refine the 
       boundaries of the artifacts. This function was implemented using scikit
       instead of opencv as 'boundary_refinement_watershed'.

       Args:
            X (4D Numpy array): original data to guide the watershed.
            E.g. (img_number, x, y, channels).

            Y_pred (4D Numpy array): predicted data to refine the boundaries.
            E.g. (img_number, x, y, channels).

            save_marks_dir (str, optional): directory to save the markers used 
            to make the watershed. Useful for debugging. 

        Return:
            watershed_predictions (4D Numpy array): refined boundaries of the 
            predictions.  E.g. (img_number, x, y, channels).
    """

    if save_marks_dir is not None:
        os.makedirs(save_marks_dir, exist_ok=True)

    watershed_predictions = np.zeros(Y_pred.shape[:3], dtype=np.uint8)
    d = len(str(X.shape[0]))

    for i in tqdm(range(X.shape[0])):
        
        im = (X[i,...,0]*255).astype(np.uint8)
        pred = (Y_pred[i,...,0]*255).astype(np.uint8)
        
        # find continuous region
        markers = rank.gradient(pred, disk(12)) < 10
        markers = ndi.label(markers)[0]

        # local gradient (disk(2) is used to keep edges thin)
        gradient = rank.gradient(im, disk(2))

        # process the watershed
        labels = watershed(gradient, markers)

        if save_marks_dir is not None:
            f = os.path.join(save_marks_dir, "mark_" + str(i).zfill(d) + ".png")
            cv2.imwrite(f, markers)
       
        watershed_predictions[i] = labels

    # Label all artifacts into 1 and the background with 0
    watershed_predictions[watershed_predictions==1] = 0
    watershed_predictions[watershed_predictions>1] = 1
    
    return np.expand_dims(watershed_predictions, -1)


def calculate_z_filtering(data, mf_size=5):
    """Applies a median filtering in the z dimension of the provided data.

       Args:
            data (4D Numpy array): data to apply the filter to.
            E.g. (image_number, x, y, channels).

            mf_size (int, optional): size of the median filter. Must be an odd
            number.

       Returns:
            out_data (4D Numpy array): data resulting from the application of
            the median filter. E.g. (image_number, x, y, channels).
    """

    out_data = np.copy(data)
    out_data = np.squeeze(out_data)

    # Must be odd
    if mf_size % 2 == 0:
       mf_size += 1

    for i in range(data.shape[2]):
        sl = (data[:, :, i, 0]).astype(np.float32)
        sl = cv2.medianBlur(sl, mf_size)
        out_data[:, :, i] = sl

    return np.expand_dims(out_data, axis=-1)

