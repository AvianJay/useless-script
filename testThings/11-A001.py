n = int(input())
for i in range(n):
  data = [int(j) for j in input().split()]
  data.pop(0)
  h, l, ll = 0, 0, 0
  for index, j in enumerate(data):
    if index == 0 or index == len(data) - 1:
      continue
    if j > data[index - 1] and j > data[index + 1] and j > 0:
      h += 1
    elif j < data[index - 1] and j < data[index + 1]:
      if j >= 0:
        l += 1
      else:
        ll += 1
  print(h, l, ll)