# DSA in JavaScript — Fast-Track Guide

> For developers fluent in Python or C++ who want to practice Data Structures & Algorithms in JavaScript

JavaScript is no longer just a frontend language. With Node.js, Deno, and its dominance in full-stack and interview settings, **knowing how to implement DSA patterns in JS** is a high-leverage skill — especially when you're already strong in algorithmic thinking.

This guide skips the fluff. It focuses only on what’s different, what matters, and how to map your existing DSA knowledge cleanly into JavaScript.

---

## 🔑 Core Syntax Mapping (C++/Python → JS)

| Concept           | Python/C++ Style              | JavaScript Equivalent                     | Notes |
|-------------------|-------------------------------|-------------------------------------------|-------|
| Variable          | `int x = 5;` / `x = 5`        | `let x = 5;` or `const x = 5;`            | Prefer `const` unless reassigning |
| Function          | `def func():` / `void func()` | `function func() {}` or `const func = () => {}` | Arrow functions are idiomatic |
| Arrays            | `vector<int>` / `list`        | `const arr = [1, 2, 3];`                  | Dynamic, zero-indexed |
| Object (struct)   | `class Node` / `dict`         | `const node = { val: 5, next: null };`    | No class needed for simple data |
| Loop              | `for i in range(n):`          | `for (let i = 0; i < arr.length; i++)` or `for (const x of arr)` | Use `for...of` for iteration |
| Map               | `unordered_map` / `dict`      | `const map = new Map(); map.set(key, val)` | Use `.has()`, `.get()`, `.set()` |
| Set               | `set`                         | `const set = new Set(); set.add(val)`     | Use `.has()` and `.add()` |
| Conditionals      | Standard                      | Same as C++                                 | `===` for strict equality |

---

## 🧠 Essential Array Methods (Replace Manual Loops)

JavaScript’s array methods make code **cleaner and less error-prone**. Use them to replace manual loops where possible.

### `map()` – Transform Each Element
```js
const squares = [1, 2, 3].map(x => x * x);
// → [1, 4, 9]
```

### `filter()` – Keep Matching Elements
```js
const evens = [1, 2, 3, 4].filter(x => x % 2 === 0);
// → [2, 4]
```

### `reduce()` – Aggregate to Single Value
```js
const sum = [1, 2, 3].reduce((acc, x) => acc + x, 0);
// → 6
```

### `find()` – First Match
```js
const firstEven = [1, 3, 2, 4].find(x => x % 2 === 0);
// → 2
```

### `some()` / `every()` – Boolean Checks
```js
[1, 2, 3].some(x => x > 2);   // true
[1, 2, 3].every(x => x > 0);  // true
```

> ✅ **Rule of thumb**: If you're looping just to transform, filter, or check, use the built-in method.

---

## 🔄 Two Sum – Side-by-Side Comparison

### Python
```python
def two_sum(nums, target):
    seen = {}
    for i, num in enumerate(nums):
        if target - num in seen:
            return [seen[target - num], i]
        seen[num] = i
```

### JavaScript (Map version)
```js
function twoSum(nums, target) {
  const seen = new Map();
  for (let i = 0; i < nums.length; i++) {
    const complement = target - nums[i];
    if (seen.has(complement)) {
      return [seen.get(complement), i];
    }
    seen.set(nums[i], i);
  }
}
```

### JavaScript (Object version – simpler for primitives)
```js
function twoSum(nums, target) {
  const seen = {};
  for (let i = 0; i < nums.length; i++) {
    const complement = target - nums[i];
    if (complement in seen) {
      return [seen[complement], i];
    }
    seen[nums[i]] = i;
  }
}
```

> 💡 Use `in` with objects, `has()` with `Map`. `Map` allows any key type; objects are faster for string/number keys.

---

## 💻 Run & Test Locally

Save as `two-sum.js`:
```js
console.log(twoSum([2, 7, 11, 15], 9)); // [0, 1]
```

Run in terminal:
```bash
node two-sum.js
```

No setup needed — `node` is preinstalled on most dev machines.

---

## 🛠️ Common DSA Patterns in JS

### 1. Sliding Window
```js
function maxSubarraySum(arr, k) {
  let sum = 0;
  for (let i = 0; i < k; i++) sum += arr[i];
  let max = sum;
  for (let i = k; i < arr.length; i++) {
    sum += arr[i] - arr[i - k];
    max = Math.max(max, sum);
  }
  return max;
}
```

### 2. Two Pointers
```js
function sortedSquares(nums) {
  const result = [];
  let left = 0, right = nums.length - 1;
  for (let i = nums.length - 1; i >= 0; i--) {
    if (Math.abs(nums[left]) > Math.abs(nums[right])) {
      result[i] = nums[left] ** 2;
      left++;
    } else {
      result[i] = nums[right] ** 2;
      right--;
    }
  }
  return result;
}
```

### 3. BFS (Tree Level-Order)
```js
function levelOrder(root) {
  if (!root) return [];
  const result = [], queue = [root];
  while (queue.length) {
    const level = [], size = queue.length;
    for (let i = 0; i < size; i++) {
      const node = queue.shift();
      level.push(node.val);
      if (node.left) queue.push(node.left);
      if (node.right) queue.push(node.right);
    }
    result.push(level);
  }
  return result;
}
```

> ⚠️ Note: `queue.shift()` is O(n). For performance, use an array with index tracking or a real queue.

---

## 🚀 Your First JS DSA Session (60 mins)

### Goal
Reimplement **2–3 DSA problems** you’ve solved in Python/C++ using JavaScript.

### Steps
1. Pick problems: Two Sum, Reverse String, Max Subarray, etc.
2. Write in a `.js` file
3. Run with `node`
4. Compare style and structure to your original

### Tools
- Editor: VS Code (with JS syntax support)
- Terminal: `node <filename>.js`
- Practice site: LeetCode (set language to JavaScript)

### Outcome
- Confidence in JS syntax for DSA
- First entry in your JS DSA practice log

---

## 🔗 Next Steps
- [ ] Save this guide
- [ ] Complete first session (scheduled below)
- [ ] Log 3 more problems over the next week
- [ ] Switch one weekly DSA session to JS permanently

You're not learning algorithms from scratch — you're **translating your strength into a new language**. That’s power.
