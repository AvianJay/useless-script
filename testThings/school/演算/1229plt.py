import matplotlib.pyplot as plt
import matplotlib as mpl

import numpy as np
import time

def sort_performance(size):
    print(f"Testing sort performance for size: {size}")
    rs = time.perf_counter()
    s = np.random.randint(1, 1001, size=size).tolist()

    for i in range(len(s)):
        for j in range(i+1, len(s)):
            # print(f"i: {s[i]}, j: {s[j]}")
            if s[i] > s[j]:
                s[i], s[j] = s[j], s[i]
    se = time.perf_counter()
    sd = se - rs
    return sd

test_sizes = [5000, 10000, 15000, 20000, 25000, 30000]
sort_times = [sort_performance(size) for size in test_sizes]
print("\n".join([f"Size: {size}, Time: {round(t, 2)}s" for size, t in zip(test_sizes, sort_times)]))
# Plotting the results

mpl.rcParams['font.sans-serif'] = ['Microsoft JhengHei']  # 用來正常顯示中文
mpl.rcParams['axes.unicode_minus'] = False  # 用來正常顯示負號
mpl.rcParams['figure.dpi'] = 150  # 顯示分辨率
mpl.rcParams['figure.figsize'] = (6, 4)  # 畫布尺寸
mpl.rcParams['savefig.dpi'] = 300  # 保存圖片分辨率
mpl.rcParams['savefig.format'] = 'png'  # 保存圖片格式
mpl.rcParams['lines.linewidth'] = 1.5  # 線寬
mpl.rcParams['lines.markersize'] = 6  # 點大小
mpl.rcParams['font.size'] = 10  # 字體大小
mpl.rcParams['legend.fontsize'] = 10  # 圖例字體大小
mpl.rcParams['axes.titlesize'] = 12  # 標題字體大小
mpl.rcParams['axes.labelsize'] = 10  # 坐標軸標籤字體大小
mpl.rcParams['xtick.labelsize'] = 9  # x軸刻度字體大小
mpl.rcParams['ytick.labelsize'] = 9  # y軸刻度字體大小
mpl.rcParams['legend.loc'] = 'best'  # 圖例位置
mpl.rcParams['legend.frameon'] = True  # 圖例邊框
plt.title("排序性能測試")
plt.xlabel("數據大小")
plt.ylabel("排序時間 (秒)")
plt.grid(True)
plt.plot(test_sizes, sort_times, marker='o')
plt.legend(["排序時間"])
plt.show()