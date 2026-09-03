---
name: modern-cpp
description: C++11 and later merge-request review. Load only after the dialect is confirmed as C++11, C++14, C++17, C++20, or C++23 from AGENTS.md, CODE_REVIEW.md, or build flags (CMAKE_CXX_STANDARD, -std=c++1x/2x). Do not load for C++98/03. Do not load when the standard is unknown. Never suggest features newer than the confirmed standard.
license: MIT
compatibility: opencode
---

# Modern C++ review (C++11 and later)

You are reviewing a change whose **confirmed** standard is C++11 or newer.
That confirmed standard is a hard ceiling. Do not “fix” C++17 with C++20.

Project rules already read this turn still win if they conflict with this skill.

Ground this review in the C++ Core Guidelines (RAII, ownership in the type
system, no dangling views) and common modern-C++ review guides (unique_ptr
by default, make_unique/make_shared, string_view/span lifetime). This is
not a modernizer pass.

## Dialect lock

State the confirmed standard (for example C++17). Only suggest features
that exist **there**:

| Standard | You may use (not a complete list) |
|---|---|
| C++11 | `auto`, range-for, lambdas, `nullptr`, `override`/`final`, `unique_ptr`/`shared_ptr`/`make_shared`, move, `enum class`, C++11 `constexpr`, `std::thread`/`mutex`/`atomic`/`lock_guard` |
| C++14 | generic lambdas, relaxed `constexpr`, `std::make_unique`, return type deduction |
| C++17 | `optional`/`variant`/`string_view`, structured bindings, `if constexpr`, guaranteed copy elision, `std::filesystem`, `std::lock` + `scoped_lock` |
| C++20 | concepts, ranges, coroutines, `std::span`, `std::jthread`, spaceship, `constexpr` allocations as the impl allows |
| C++23 | `std::expected`, `std::mdspan`, `std::move_only_function`, explicit object parameters |

If the only nice fix needs a newer standard, say so and give a fix that
compiles at the confirmed level.

`std::auto_ptr` is deprecated (removed in C++17). Prefer `unique_ptr` at
C++11+.

## What to look for

### Ownership and RAII (Core Guidelines R / C)

Decision matrix (non-owning vs owning):

| Situation | Prefer |
|---|---|
| Single owner | `std::unique_ptr<T>` / `unique_ptr<T[]>` |
| Shared lifetime | `std::shared_ptr<T>` + `weak_ptr` to break cycles |
| Never-null observer | `T&` |
| Maybe-null observer | `T*` (no ownership) |
| C++17 read-only text | `std::string_view` **parameter**, not a stored member unless lifetime is proven |
| C++20 buffer view | `std::span<T>` with the same lifetime rule |

- Rule of zero when members already own resources. If any of destructor /
  copy / move is user-written, apply the rule of five (`= default` or
  `= delete` the rest).
- Raw `new`/`delete` in business logic: wrap with `make_unique` (C++14+)
  or `unique_ptr<T>(new T(...))` at C++11. Never write
  `unique_ptr<T>(new T)` **and** another `new` in the same full-expression
  (exception leaks the other allocation). Prefer `make_shared` when they
  already need `shared_ptr` (one allocation, exception-safe).
- Do not pass `unique_ptr` by lvalue reference to “use” it; pass `T&` /
  `T*` to observe, or `unique_ptr` **by value** to transfer.
- `get()` / `release()`: the raw pointer must not be given to another
  smart pointer or `delete`d by someone else. `release()` requires a new
  owner immediately.
- `shared_ptr(this)` without `enable_shared_from_this` → double-delete.
- `shared_ptr` where unique ownership was enough (refcount tax + cycles).
- Move operations should be `noexcept` when they truly cannot throw
  (otherwise `vector` will copy on reallocation).
- A virtual destructor on a polymorphic base: `virtual ~T() = default`
  does **not** by itself require deleting copies; default the other
  special members if the type should stay copyable.

### Moves

- Use-after-move (parameter or member used after `std::move`).
  A moved-from object is valid only for destroy and assign; do not read it.
- `return std::move(local)` blocks elision. Return the local.
- `std::move` on a `const` object silently copies.
- `std::forward` only on a forwarding reference (`T&&` in a deduced
  context). Missing `forward` in a wrapper, or `forward` on an `T&`.
- Sink parameter: take `T` by value and move into the member (C++11),
  not `const T&` plus a manual copy, when they already store `T`.

### Lifetime and views

Highest-signal modern bugs (CppCon “Enough string_view to Hang
Ourselves”, Core Guidelines lifetime profile):

- `string_view` / `span` / `reference_wrapper` bound to a temporary
  (`std::string` method chained into a view; `std::string_view v = foo();`
  when `foo` returns `string`).
- Storing `string_view` / `span` in a member or container when the
  backing `string`/`vector` can reallocate or die.
- Range-for over a temporary that yields a view:
  `for (auto& x : getVec())` is OK; `for (auto c : getString().substr(1))`
  / a filter on a temporary is not.
- Lambda `[=]`, `[&]`, or `this` captured and stored (`std::function`,
  `std::async`, a member callback) after the locals / `*this` die.
  C++20 deprecates implicit `this` in `[=]`.
- `std::initializer_list` backing array dies at the end of the full
  expression; do not store the list.
- Iterator/reference invalidation after `push_back`, `rehash`, `erase`.

### Concurrency (C++11+)

Core Guidelines CP:

- RAII locks only (`lock_guard` / `unique_lock` / C++17 `scoped_lock`).
  Flag bare `mutex.lock()` / `unlock()`.
- Two mutexes: `std::lock` + `adopt_lock`, or C++17 `scoped_lock(m1, m2)`.
  Opposite lock order is a deadlock.
- Do not call unknown code (virtual, callback, foreign function) while
  holding a lock.
- `std::thread` must be joined or ownership transferred. Prefer C++20
  `std::jthread` when that standard is confirmed. Flag `detach()`.
- `std::async` / a thread that captures stack locals by reference.
- `shared_ptr` control block is atomic; the **object** and a given
  `shared_ptr` instance are not safe for concurrent write.
- `std::atomic` on a counter does not make a surrounding struct
  thread-safe. Relaxed atomics need a proven acquire/release pairing.

### Undefined behavior and types

- Signed overflow, OOB, `reinterpret_cast` aliasing, uninitialized
  reads (including a defaulted special member that skipped a field).
- `const_cast` that writes an object that was born `const`.
- Brace init that narrows.
- `dynamic_cast` to a reference on failure (throws).
- Missing `override` on a function that is meant to override (signature
  drift creates a new virtual instead).
- `NULL` vs `nullptr` when an `int` / pointer overload set exists.

### Library pitfalls (only if that header is already in play)

- `optional::value()` / `operator*` on empty; prefer a check or
  `value_or`.
- `std::function` storing a move-only lambda (needs C++23
  `move_only_function`; do not suggest it earlier).
- I/O or `filesystem` calls that ignore errors when the code claims to
  handle failure.
- `emplace_back` vs `push_back` only when it avoids an extra copy/move
  of a heavy type or a correctness issue, not as a style nit.

### C++20+ only when confirmed

- Coroutine: ref/view across `co_await`; handle used after the frame
  is destroyed; missing `co_return`.
- Ranges adaptor on a temporary (`views::filter` / `split` dangling).
- Concept too loose; the body then misuses the type.
- `std::span` constructed from a temporary container.

### Tests

- New move/copy, empty optional/view, and concurrent paths without a
  test. ASan/TSan mentions are fine if the project already runs them;
  do not demand a new sanitizer setup.

## Do not flag

- “Rewrite this in a newer standard than the project uses.”
- A correct index loop or C API the project already depends on.
- Style, naming, include order unless this repo’s rules say so.
- Pre-existing issues outside this diff.
- `emplace` / `reserve` / `[[nodiscard]]` as a standalone nit unless
  they fix a real copy, realloc, or ignored return.
