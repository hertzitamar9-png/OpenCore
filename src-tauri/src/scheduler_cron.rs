//! Five-field cron evaluated on UTC instants. Local DST gaps are skipped and
//! repeated wall-clock minutes are separate occurrences with distinct UTC IDs.
use chrono::{Datelike, Local, TimeZone, Timelike, Utc};

#[derive(Clone, Debug)]
struct Field { values: Vec<bool>, wildcard: bool }
impl Field {
    fn parse(text: &str, min: usize, max: usize, sunday: bool) -> Result<Self, String> {
        let mut values = vec![false; max + 1];
        if text.is_empty() { return Err("Empty cron field".into()); }
        for segment in text.split(',') {
            let parts: Vec<_> = segment.split('/').collect();
            if parts.len() > 2 { return Err("Invalid cron step".into()); }
            let step = if parts.len() == 2 { parts[1].parse::<usize>().map_err(|_| "Invalid cron step")? } else { 1 };
            if step == 0 || step > max + 1 { return Err("Cron step is out of range".into()); }
            let (start, end) = if parts[0] == "*" { (min, max) } else {
                let range: Vec<_> = parts[0].split('-').collect();
                let start = range[0].parse::<usize>().map_err(|_| "Cron fields must be numbers, ranges or *")?;
                let end = match range.len() { 1 => if parts.len() == 2 { max } else { start }, 2 => range[1].parse::<usize>().map_err(|_| "Invalid cron range")?, _ => return Err("Invalid cron range".into()) };
                (start, end)
            };
            if start < min || end > max || start > end { return Err("Cron field is out of range".into()); }
            for number in (start..=end).step_by(step) { values[if sunday && number == 7 { 0 } else { number }] = true; }
        }
        Ok(Self { values, wildcard: text.starts_with('*') })
    }
    fn has(&self, number: u32) -> bool { self.values.get(number as usize).copied().unwrap_or(false) }
}

#[derive(Clone, Debug)]
pub struct CronSchedule { minute: Field, hour: Field, day: Field, month: Field, weekday: Field, local: bool }
impl CronSchedule {
    pub fn parse(expression: &str, timezone: &str) -> Result<Self, String> {
        let fields: Vec<_> = expression.split_whitespace().collect();
        if fields.len() != 5 { return Err("Cron must have five fields: minute hour day month weekday".into()); }
        if !matches!(timezone, "utc" | "local") { return Err("Cron timezone must be utc or local".into()); }
        Ok(Self { minute: Field::parse(fields[0], 0, 59, false)?, hour: Field::parse(fields[1], 0, 23, false)?,
            day: Field::parse(fields[2], 1, 31, false)?, month: Field::parse(fields[3], 1, 12, false)?,
            weekday: Field::parse(fields[4], 0, 7, true)?, local: timezone == "local" })
    }
    pub fn next_after(&self, after: i64) -> Result<i64, String> {
        if self.local { self.next_with_clock(after, |instant| Local.timestamp_millis_opt(instant).single().unwrap()) }
        else { self.next_with_clock(after, |instant| Utc.timestamp_millis_opt(instant).single().unwrap()) }
    }
    fn next_with_clock<T: TimeZone, F: Fn(i64) -> chrono::DateTime<T>>(&self, after: i64, clock: F) -> Result<i64, String> {
        let mut instant = after.div_euclid(60_000).checked_add(1).and_then(|value| value.checked_mul(60_000)).ok_or("Schedule time is out of range")?;
        // Gregorian leap-day schedules occur within eight years, including 2100.
        let limit = instant.checked_add(8 * 366 * 86_400_000i64).ok_or("Schedule time is out of range")?;
        if Utc.timestamp_millis_opt(instant).single().is_none() || Utc.timestamp_millis_opt(limit).single().is_none() { return Err("Schedule time is out of range".into()); }
        // Reject impossible dates without scanning millions of minutes.
        if self.weekday.wildcard && !self.day.wildcard && !(1..=12).any(|month| {
            let max_day = match month { 2 => 29, 4 | 6 | 9 | 11 => 30, _ => 31 };
            self.month.has(month) && (1..=max_day).any(|day| self.day.has(day))
        }) { return Err("Cron has no valid calendar date".into()); }
        while instant <= limit {
            let date = clock(instant);
            let dom = self.day.has(date.day()); let dow = self.weekday.has(date.weekday().num_days_from_sunday());
            let day_matches = if self.day.wildcard || self.weekday.wildcard { dom && dow } else { dom || dow };
            if self.month.has(date.month()) && day_matches && self.hour.has(date.hour()) && self.minute.has(date.minute()) { return Ok(instant); }
            instant += 60_000;
        }
        Err("Cron has no occurrence within eight years".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn ms(value: &str) -> i64 { chrono::DateTime::parse_from_rfc3339(value).unwrap().timestamp_millis() }

    #[test]
    fn cron_is_strictly_after_the_current_minute_and_rolls_years() {
        let cron = CronSchedule::parse("0 0 1 1 *", "utc").unwrap();
        assert_eq!(cron.next_after(ms("2026-01-01T00:00:00Z")).unwrap(), ms("2027-01-01T00:00:00Z"));
        let every = CronSchedule::parse("*/15 * * * *", "utc").unwrap();
        assert_eq!(every.next_after(ms("2026-10-06T23:59:59Z")).unwrap(), ms("2026-10-07T00:00:00Z"));
    }

    #[test]
    fn calendar_and_weekday_match_use_standard_or_semantics() {
        let cron = CronSchedule::parse("30 9 15 * 1", "utc").unwrap();
        assert_eq!(cron.next_after(ms("2026-10-06T00:00:00Z")).unwrap(), ms("2026-10-12T09:30:00Z"));
        assert_eq!(CronSchedule::parse("0 0 * * 7", "utc").unwrap().next_after(ms("2026-10-06T00:00:00Z")).unwrap(), ms("2026-10-11T00:00:00Z"));
    }

    #[test]
    fn rejects_invalid_fields_zero_steps_and_impossible_dates() {
        for expression in ["* * * *", "60 * * * *", "0 24 * * *", "*/0 * * * *", "9-2 * * * *", "0 0 0 * *", "0 0 * 13 *", "0 0 * * 8"] {
            assert!(CronSchedule::parse(expression, "utc").is_err(), "{expression}");
        }
        assert!(CronSchedule::parse("* * * * *", "Europe/London").is_err());
        assert!(CronSchedule::parse("0 0 30 2 *", "utc").unwrap().next_after(ms("2026-10-06T00:00:00Z")).is_err());
    }

    #[test]
    fn local_uses_the_operating_system_zone_at_each_occurrence() {
        let start = ms("2026-10-06T00:00:00Z");
        let cron = CronSchedule::parse("17 9 * * *", "local").unwrap();
        let next = chrono::Local.timestamp_millis_opt(cron.next_after(start).unwrap()).single().unwrap();
        assert_eq!((next.hour(), next.minute(), next.second()), (9, 17, 0));
        assert!(next.timestamp_millis() > start);
    }

    #[test]
    fn cron_skips_a_dst_gap_and_admits_both_fall_back_minutes() {
        // A synthetic offset change makes the test independent of the CI machine's timezone.
        let cron = CronSchedule::parse("30 2 * * *", "utc").unwrap();
        let gap = ms("2026-03-29T00:00:00Z");
        let next = cron.next_with_clock(gap, |instant| {
            let offset = if instant < ms("2026-03-29T01:00:00Z") { 3600 } else { 7200 };
            Utc.timestamp_millis_opt(instant + offset * 1000).single().unwrap()
        }).unwrap();
        assert_eq!(next, ms("2026-03-30T00:30:00Z"));
        let fallback = CronSchedule::parse("30 2 * * *", "utc").unwrap();
        let clock = |instant| {
            let offset = if instant < ms("2026-10-25T01:00:00Z") { 7200 } else { 3600 };
            Utc.timestamp_millis_opt(instant + offset * 1000).single().unwrap()
        };
        let first = fallback.next_with_clock(ms("2026-10-24T23:00:00Z"), clock).unwrap();
        let second = fallback.next_with_clock(first, clock).unwrap();
        assert_eq!(first, ms("2026-10-25T00:30:00Z"));
        assert_eq!(second, ms("2026-10-25T01:30:00Z"));
    }
}
