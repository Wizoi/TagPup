import numpy as np
import time

def geometric_median(X, eps=1e-5, max_iter=20):
    """
    X is a 2D numpy array of shape (N, D).
    Returns the geometric median of the vectors.
    """
    # Initial guess is the mean
    y = np.mean(X, axis=0)
    for i in range(max_iter):
        distances = np.linalg.norm(X - y, axis=1)
        # Handle division by zero
        zero_mask = distances < 1e-10
        if np.any(zero_mask):
            distances = np.where(zero_mask, 1e-10, distances)
        
        weights = 1.0 / distances
        weights_sum = np.sum(weights)
        next_y = np.sum(X * weights[:, np.newaxis], axis=0) / weights_sum
        
        if np.linalg.norm(next_y - y) < eps:
            # print(f"Converged in {i+1} iterations.")
            return next_y
        y = next_y
    return y

def benchmark():
    # 5000 random vectors of 512 dimensions
    X = np.random.rand(5000, 512).astype(np.float32)
    
    t0 = time.time()
    mean_val = np.mean(X, axis=0)
    print(f"Mean computation took: {time.time() - t0:.6f} seconds")
    
    t0 = time.time()
    geom_median = geometric_median(X, max_iter=10)
    print(f"Geometric Median (10 iterations) took: {time.time() - t0:.6f} seconds")

if __name__ == "__main__":
    benchmark()
