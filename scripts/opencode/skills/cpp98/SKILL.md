---
name: cpp98
description: C++98/03 merge-request review. Load only after the dialect is confirmed as C++98 or C++03 from AGENTS.md, CODE_REVIEW.md, or build flags (-std=c++98, -std=c++03, CMAKE_CXX_STANDARD 98). Do not load for C++11 or later, or when the standard is unknown.
license: MIT
compatibility: opencode
---

# C++98 / C++03 review

You are reviewing a **C++98 or C++03** change. That dialect is a hard ceiling.

Project rules already read this turn still win if they conflict with this skill.

Ground this review in C++98-era practice (Sutter RAII / “one resource per
statement”, Meyers Effective C++ rule of three, Boost.SmartPtr if Boost is
already a dependency). Do not modernize.

## Dialect lock

Legal: C++98/03, the C++98 standard library, compiler extensions the repo
already uses.

**Do not recommend** C++11 or later, including:

- `auto` (type deduction), range-for, lambdas, `nullptr`
- `override`, `final`, `default`/`delete` on special members
- `std::unique_ptr`, `std::shared_ptr`, `make_shared` / `make_unique`
- move semantics, rvalue references, `std::move`, `std::forward`
- `constexpr`, `static_assert`, `decltype`, `noexcept`
- `std::thread`, `std::mutex`, `std::atomic` (as the suggested API)
- `std::array`, `std::unordered_map`, `std::function`, `std::tuple`
- braced init, delegating / inheriting constructors
- `enum class`, C++11 `using` aliases, `long long` unless the tree already
  uses that compiler extension

If the only “fix” needs a newer standard, say that and give a **C++98-legal**
alternative, or skip the finding.

`std::auto_ptr` is legal. Never replace it with `std::unique_ptr`. Flag
`auto_ptr` copy (ownership moves on copy; the source becomes empty). If
Boost is already in the project, prefer the matching Boost pointer over
new `auto_ptr` or a new owning raw pointer.

## Boost (only if already used)

Do **not** add Boost as a new dependency. Decide from files you may already
open (skip missing):

- `#include <boost/...>` in the changed files or nearby headers
- `find_package(Boost`, `Boost::`, or `boost_` in `CMakeLists.txt`
- `boost` in `conanfile.txt`, `conanfile.py`, or `vcpkg.json`

If Boost is absent: C++98 RAII / a small handle class / documented raw
ownership. Do not mention Boost.

If Boost **is** used, prefer Boost.SmartPtr over a new owning `new`/`delete`:

| Situation | Prefer | Avoid |
|---|---|---|
| Exclusive owner, not copied | `boost::scoped_ptr<T>` | raw owning `T*` |
| Exclusive owner of an array | `boost::scoped_array<T>` | raw `T*` + `delete[]` |
| Shared ownership | `boost::shared_ptr<T>` + `boost::weak_ptr<T>` observers | raw shared `T*` |
| Shared array | `boost::shared_array<T>` | manual refcount on an array |
| Type already has an intrusive count | `boost::intrusive_ptr<T>` | inventing intrusive counts |

`scoped_ptr` is not copyable; `shared_ptr` is. Do not suggest `shared_ptr`
for a single owner.

Still flag:

- `shared_ptr` cycles (need `weak_ptr`)
- `shared_ptr(raw)` / `scoped_ptr` reset when something else already owns `raw`
- `shared_ptr(this)` without `boost::enable_shared_from_this`
- `px.get()` passed into another smart pointer (double-delete)
- After `reset()` / `px = other`, using the old raw `get()`
- `boost::noncopyable` types that still got a compiler-generated copy

If they already use Boost.Thread / Boost.Mutex: RAII `boost::mutex::scoped_lock`
(or the equivalent they already include). Do not suggest `std::lock_guard`.

## What to look for

### RAII and special members (rule of three)

Sutter: a resource is owned by an object; never allocate two resources in
one statement (if the second `new` throws, the first leaks).

- User-written destructor, copy ctor, or copy assign → usually all three.
  Missing one is often a leak or double-free.
- Prefer the rule of zero when members already own everything
  (`std::vector`, `std::string`, a Boost pointer).
- Polymorphic base (`virtual` method) without a virtual destructor.
- Slicing: derived object copied/passed as the base **by value**.
- Self-assignment in `operator=` (`this != &other` or copy-and-swap).
- Member initializer list missing fields, or listed in a different order
  than declaration order (init follows declaration order).
- Uninitialized POD members, locals, or buffers.

### Memory and C strings

- `new`/`delete` and `new[]`/`delete[]` must match.
- Every `new` needs an owner. A raw owning return with no documented
  contract is a leak on the early-return / throw path.
- Exception between `new` and the owner: the pointer is lost.
- Double-delete, use-after-free, returning a pointer/reference to a local.
- `strcpy`, `sprintf`, `gets`, unchecked `memcpy` / `strncpy` that may
  omit the NUL. Prefer `std::string` / `std::vector` when the file already
  uses them.

### Undefined behavior

- Signed overflow; shift into or past the sign bit.
- Sequence points: `i = i++`, `a[i] = i++`.
- Strict aliasing / type punning via an incompatible pointer or a
  non-POD `union`. `memcpy` into a local is the C++98-safe pattern.
- `memcpy` / `memset` on a type with a user ctor/dtor (not POD).
- Iterator/reference invalidation after `vector`/`string`/`deque`
  reallocation or `erase`.
- Out-of-bounds index; `front`/`back` on empty.
- Pure virtual called from a constructor or destructor.
- `delete` of an incomplete type (destructor not run).

### Types, conversions, APIs

- C-style casts that hide `const_cast` or a wrong downcast. Prefer
  `static_cast` / `const_cast` / `reinterpret_cast` (all C++98).
- `dynamic_cast` on a non-polymorphic type; unchecked `dynamic_cast` to a
  reference (throws).
- Converting constructors that should be `explicit`.
- Integer truncation; `size_t` vs `int` in loop bounds
  (`i < v.size()` with signed `i` can warn and wrap).
- `NULL` passed to an overload set that has both `int` and `T*` (may
  call the `int` overload). Only flag when that overload exists.
- `vector<bool>` proxies: do not take `bool&` from it.
- Read-only large objects: `const T&`. Nullable observer: `T*` with a
  documented null. Transfer of a raw owner: document it; do not invent
  `optional`.

### Headers, templates, preprocessor

- Missing or colliding include guards.
- `using namespace` in a header.
- Non-inline function or static data defined in a header (ODR).
- Forward-declare instead of including when only a pointer/reference is
  needed (Sutter: minimize definitional dependencies).
- Macro that evaluates an argument twice or lacks parentheses. Prefer
  `const`, `enum`, or `inline` over `#define` when they work.
- Dependent names in templates missing `typename` / `template`
  (two-phase lookup). Breaks GCC/Comeau even if MSVC accepted it.
- `for (int i = 0; ...)` then using `i` after the loop (illegal in
  standard C++98; old MSVC leaked the name).

### Exceptions

- `new` throws `std::bad_alloc` unless they use nothrow new.
- A destructor that throws (including a member destructor during
  stack unwinding).
- Constructor that acquires resource A, then throws before B is owned.
- `catch (...)` that swallows and continues in a bad state. Letting the
  exception propagate is better than swallowing.
- Exception specifications (`throw()`) that do not match what callees
  actually throw.

### Concurrency

C++98 has no standard threads. If the diff uses pthreads, Win32, or
Boost.Thread: data races, missing join, unlock of a mutex this thread
does not hold, shared `std::string`/containers without a lock. Do not
suggest `std::thread`. Static initialization order across TUs can race
before `main`.

### Tests

- New copy/assign, empty container, and allocation-failure paths (if
  they claim to handle them) without a test.

## Fixes must compile as C++98

Show a short C++98 snippet when the fix is not obvious. STL algorithms
from `<algorithm>` / `<numeric>` are legal (`std::find`, `std::copy`,
`std::transform`). Do not “fix” with range-for or lambdas.

## Do not flag

- “This would be cleaner in modern C++.”
- Index `for` loops that are correct (no range-for in this dialect).
- Style, naming, braces, include order unless this repo’s rules say so.
- `NULL` vs `0` except the overload bug above.
- Pre-existing issues outside this diff.
