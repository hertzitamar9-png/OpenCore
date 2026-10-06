/// Exact shortest line edit distance. No approximate counts are returned when the work budget is exhausted.
pub(super) fn line_counts(before: &str, after: &str, budget: usize) -> Result<(u64, u64), String> {
    line_counts_with_work(before, after, budget).map(|(added, removed, _)| (added, removed))
}

pub(super) fn line_counts_with_work(
    before: &str,
    after: &str,
    budget: usize,
) -> Result<(u64, u64, usize), String> {
    if before.is_empty() || after.is_empty() {
        return Ok((
            after.split_inclusive('\n').count() as u64,
            before.split_inclusive('\n').count() as u64,
            0,
        ));
    }
    let left: Vec<&str> = before.split_inclusive('\n').take(200_001).collect();
    let right: Vec<&str> = after.split_inclusive('\n').take(200_001).collect();
    if left.len() > 200_000 || right.len() > 200_000 {
        return Err(
            "Exact line diff line traversal limit reached; line counts are unavailable".into(),
        );
    }
    let mut work = 0usize;
    let mut prefix = 0;
    while prefix < left.len().min(right.len()) && left[prefix] == right[prefix] {
        prefix += 1;
        work += 1;
        if work > budget {
            return Err("Exact line diff work limit reached; line counts are unavailable".into());
        }
    }
    let mut left_end = left.len();
    let mut right_end = right.len();
    while left_end > prefix && right_end > prefix && left[left_end - 1] == right[right_end - 1] {
        left_end -= 1;
        right_end -= 1;
        work += 1;
        if work > budget {
            return Err("Exact line diff work limit reached; line counts are unavailable".into());
        }
    }
    let left = &left[prefix..left_end];
    let right = &right[prefix..right_end];
    let n = left.len() as isize;
    let m = right.len() as isize;
    if n == 0 || m == 0 {
        return Ok((m as u64, n as u64, work));
    }
    let max = n + m;
    let offset = max + 1;
    let mut frontier = vec![0isize; (2 * max + 3) as usize];
    for distance in 0..=max {
        let mut diagonal = -distance;
        while diagonal <= distance {
            work += 1;
            if work > budget {
                return Err(
                    "Exact line diff work limit reached; line counts are unavailable".into(),
                );
            }
            let index = (offset + diagonal) as usize;
            let mut x = if diagonal == -distance
                || (diagonal != distance && frontier[index - 1] < frontier[index + 1])
            {
                frontier[index + 1]
            } else {
                frontier[index - 1] + 1
            };
            let mut y = x - diagonal;
            while x < n && y < m && x >= 0 && y >= 0 && left[x as usize] == right[y as usize] {
                x += 1;
                y += 1;
                work += 1;
                if work > budget {
                    return Err(
                        "Exact line diff work limit reached; line counts are unavailable".into(),
                    );
                }
            }
            frontier[index] = x;
            if x >= n && y >= m {
                return Ok((
                    ((distance + m - n) / 2) as u64,
                    ((distance + n - m) / 2) as u64,
                    work,
                ));
            }
            diagonal += 2;
        }
    }
    Err("Could not compute the exact line diff".into())
}
