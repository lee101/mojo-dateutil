"""ISO-8601 parsing and recurrence expansion kernels."""

from std.sys.info import simd_width_of

comptime BPtr = UnsafePointer[UInt8, AnyOrigin[mut=True]]
comptime IPtr = UnsafePointer[Int64, AnyOrigin[mut=True]]


def is_leap(year: Int) -> Bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def month_length(year: Int, month: Int) -> Int:
    if month == 2:
        return 29 if is_leap(year) else 28
    if month == 4 or month == 6 or month == 9 or month == 11:
        return 30
    return 31


def ordinal_from_ymd(year: Int, month: Int, day: Int) -> Int:
    var y = year - 1
    var total = 365 * y + y // 4 - y // 100 + y // 400
    var m = 1
    while m < month:
        total += month_length(year, m)
        m += 1
    return total + day


def ymd_from_ordinal(ordinal: Int, result: IPtr):
    # Hinnant's civil calendar transform, shifted from 1970 days to ordinals.
    var z = ordinal - 719163 + 719468
    var era = z // 146097
    var doe = z - era * 146097
    var yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    var year = yoe + era * 400
    var doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    var mp = (5 * doy + 2) // 153
    var day = doy - (153 * mp + 2) // 5 + 1
    var month = mp + (3 if mp < 10 else -9)
    year += 1 if month <= 2 else 0
    result[0] = Int64(year)
    result[1] = Int64(month)
    result[2] = Int64(day)


def digit(src: BPtr, i: Int) -> Int:
    var value = Int(src[i]) - 48
    return value if value >= 0 and value <= 9 else -1


def digits(src: BPtr, begin: Int, count: Int) -> Int:
    var value = 0
    for i in range(count):
        var d = digit(src, begin + i)
        if d < 0:
            return -1
        value = value * 10 + d
    return value


def iso_weeks_in_year(year: Int) -> Int:
    var jan1 = (ordinal_from_ymd(year, 1, 1) - 1) % 7
    return 53 if jan1 == 3 or (jan1 == 2 and is_leap(year)) else 52


def parse_date(src: BPtr, n: Int, result: IPtr) -> Int:
    if n < 4:
        return 1
    var year = digits(src, 0, 4)
    if year < 1:
        return 1
    var month = 1
    var day = 1
    if n == 4:
        pass
    elif n >= 7 and Int(src[4]) == 87:  # YYYYWww[D]
        var week = digits(src, 5, 2)
        var weekday = digits(src, 7, 1) if n == 8 else 1
        if (n != 7 and n != 8) or week < 1 or week > iso_weeks_in_year(year) or weekday < 1 or weekday > 7:
            return 1
        var jan4 = ordinal_from_ymd(year, 1, 4)
        var target = jan4 - ((jan4 - 1) % 7) + (week - 1) * 7 + weekday - 1
        ymd_from_ordinal(target, result)
        return 0
    elif n >= 8 and Int(src[4]) == 45 and Int(src[5]) == 87:  # YYYY-Www[-D]
        var week = digits(src, 6, 2)
        var weekday = digits(src, 9, 1) if n == 10 and Int(src[8]) == 45 else 1
        if (n != 8 and n != 10) or week < 1 or week > iso_weeks_in_year(year) or weekday < 1 or weekday > 7:
            return 1
        var jan4 = ordinal_from_ymd(year, 1, 4)
        var target = jan4 - ((jan4 - 1) % 7) + (week - 1) * 7 + weekday - 1
        ymd_from_ordinal(target, result)
        return 0
    elif n == 7 and Int(src[4]) != 45:  # YYYYDDD
        var doy = digits(src, 4, 3)
        if doy < 1 or doy > (366 if is_leap(year) else 365):
            return 1
        ymd_from_ordinal(ordinal_from_ymd(year, 1, 1) + doy - 1, result)
        return 0
    elif n == 8 and Int(src[4]) == 45:  # YYYY-DDD
        var doy = digits(src, 5, 3)
        if doy < 1 or doy > (366 if is_leap(year) else 365):
            return 1
        ymd_from_ordinal(ordinal_from_ymd(year, 1, 1) + doy - 1, result)
        return 0
    elif n == 7 and Int(src[4]) == 45:  # YYYY-MM
        month = digits(src, 5, 2)
    elif n == 8:  # YYYYMMDD
        month = digits(src, 4, 2)
        day = digits(src, 6, 2)
    elif n == 10 and Int(src[4]) == 45 and Int(src[7]) == 45:
        month = digits(src, 5, 2)
        day = digits(src, 8, 2)
    else:
        return 1
    if month < 1 or month > 12 or day < 1 or day > month_length(year, month):
        return 1
    result[0] = Int64(year)
    result[1] = Int64(month)
    result[2] = Int64(day)
    return 0


def parse_time(src: BPtr, begin: Int, end: Int, result: IPtr) -> Int:
    var tz_begin = end
    var i = begin
    while i < end:
        var c = Int(src[i])
        if c == 90 or c == 122 or c == 43 or c == 45:
            tz_begin = i
            break
        i += 1
    var time_n = tz_begin - begin
    if time_n < 2:
        return 2
    var hour = digits(src, begin, 2)
    var minute = 0
    var second = 0
    var micros = 0
    var frac_begin = -1
    var colon = time_n >= 3 and Int(src[begin + 2]) == 58
    if time_n == 2:
        pass
    elif colon:
        if time_n < 5:
            return 2
        minute = digits(src, begin + 3, 2)
        if time_n > 5:
            if time_n < 8 or Int(src[begin + 5]) != 58:
                return 2
            second = digits(src, begin + 6, 2)
            if time_n > 8:
                frac_begin = begin + 9
                if Int(src[begin + 8]) != 46 and Int(src[begin + 8]) != 44:
                    return 2
    else:
        if time_n < 4:
            return 2
        minute = digits(src, begin + 2, 2)
        if time_n > 4:
            if time_n < 6:
                return 2
            second = digits(src, begin + 4, 2)
            if time_n > 6:
                frac_begin = begin + 7
                if Int(src[begin + 6]) != 46 and Int(src[begin + 6]) != 44:
                    return 2
    if frac_begin >= 0:
        if frac_begin >= tz_begin:
            return 2
        var used = 0
        var j = frac_begin
        while j < tz_begin:
            var d = digit(src, j)
            if d < 0:
                return 2
            if used < 6:
                micros = micros * 10 + d
                used += 1
            j += 1
        while used < 6:
            micros *= 10
            used += 1
    if hour < 0 or hour > 24 or minute < 0 or minute > 59 or second < 0 or second > 59:
        return 2
    if hour == 24 and (minute != 0 or second != 0 or micros != 0):
        return 2
    result[3] = Int64(hour)
    result[4] = Int64(minute)
    result[5] = Int64(second)
    result[6] = Int64(micros)
    result[7] = 0
    result[8] = 0
    if tz_begin < end:
        var tc = Int(src[tz_begin])
        result[8] = 1
        if tc == 90 or tc == 122:
            if tz_begin + 1 != end:
                return 3
        else:
            var remain = end - tz_begin - 1
            var th = -1
            var tm = 0
            if remain == 2:
                th = digits(src, tz_begin + 1, 2)
            elif remain == 4:
                th = digits(src, tz_begin + 1, 2)
                tm = digits(src, tz_begin + 3, 2)
            elif remain == 5 and Int(src[tz_begin + 3]) == 58:
                th = digits(src, tz_begin + 1, 2)
                tm = digits(src, tz_begin + 4, 2)
            else:
                return 3
            if th < 0 or th > 23 or tm < 0 or tm > 59:
                return 3
            result[7] = Int64((th * 3600 + tm * 60) * (1 if tc == 43 else -1))
    return 0


def mask_allows(mask: Int, value: Int) -> Bool:
    return mask == 0 or ((mask >> value) & 1) != 0


def date_allowed(
    year: Int,
    month: Int,
    day: Int,
    ordinal: Int,
    month_mask: Int,
    monthday_pos: Int,
    monthday_neg: Int,
    weekday_mask: Int,
    nth_rules: IPtr,
    nth_n: Int,
) -> Bool:
    if month_mask != 0 and ((month_mask >> month) & 1) == 0:
        return False
    var dim = month_length(year, month)
    if monthday_pos != 0 or monthday_neg != 0:
        if ((monthday_pos >> (day - 1)) & 1) == 0 and ((monthday_neg >> (dim - day)) & 1) == 0:
            return False
    if weekday_mask == 0 and nth_n == 0:
        return True
    var weekday = (ordinal - 1) % 7
    if ((weekday_mask >> weekday) & 1) != 0:
        return True
    for i in range(nth_n):
        var wanted_day = Int(nth_rules[i * 2])
        var nth = Int(nth_rules[i * 2 + 1])
        if wanted_day == weekday:
            if nth > 0 and (day - 1) // 7 + 1 == nth:
                return True
            if nth < 0 and -((dim - day) // 7 + 1) == nth:
                return True
    return False


def emit_candidate(
    ordinal: Int,
    seconds: Int,
    start_ordinal: Int,
    start_seconds: Int,
    until_ordinal: Int,
    until_seconds: Int,
    count_limit: Int,
    skip: Int,
    matched: Int,
    stored: Int,
    capacity: Int,
    result_ordinals: IPtr,
    result_seconds: IPtr,
) -> Int:
    if ordinal < start_ordinal or (ordinal == start_ordinal and seconds < start_seconds):
        return matched
    if ordinal > until_ordinal or (ordinal == until_ordinal and seconds > until_seconds):
        return -1
    var next_matched = matched + 1
    if count_limit >= 0 and next_matched > count_limit:
        return -1
    if next_matched > skip and stored < capacity:
        result_ordinals[stored] = Int64(ordinal)
        result_seconds[stored] = Int64(seconds)
    return next_matched


def parse_iso(src: BPtr, n: Int, result: IPtr) -> Int:
    comptime W = simd_width_of[DType.float64]()
    var i = 0
    var zero = SIMD[DType.int64, W](0)
    while i + W <= 9:
        result.store[width=W](i, zero)
        i += W
    while i < 9:
        result[i] = 0
        i += 1
    var split = n
    if n >= 10 and Int(src[4]) == 45 and Int(src[7]) == 45:
        split = 10
    elif n >= 10 and Int(src[4]) == 45 and Int(src[5]) == 87 and Int(src[8]) == 45:
        split = 10
    elif n > 8 and Int(src[4]) == 45:
        split = 8
    elif n >= 8 and Int(src[4]) == 87 and digit(src, 7) >= 1 and digit(src, 7) <= 7:
        split = 8
    elif n > 7 and Int(src[4]) == 87:
        split = 7
    elif n > 8 and digit(src, 7) >= 0:
        split = 8
    elif n > 7 and digit(src, 7) < 0:
        split = 7
    var status = parse_date(src, split, result)
    if status != 0:
        return status
    if split < n:
        status = parse_time(src, split + 1, n, result)
        if status != 0:
            return status
        if result[3] == 24:
            var ordinal = ordinal_from_ymd(Int(result[0]), Int(result[1]), Int(result[2])) + 1
            ymd_from_ordinal(ordinal, result)
            result[3] = 0
    return 0


@export("mdu_parse_iso")
def mdu_parse_iso(data_addr: Int, n: Int, result_addr: Int) abi("C") -> Int:
    if data_addr == 0 or result_addr == 0 or n <= 0:
        return -1
    return parse_iso(
        BPtr(unsafe_from_address=data_addr),
        n,
        IPtr(unsafe_from_address=result_addr),
    )


@export("mdu_parse_iso_many")
def mdu_parse_iso_many(
    data_addr: Int,
    offsets_addr: Int,
    data_n: Int,
    count: Int,
    results_addr: Int,
    status_addr: Int,
) abi("C") -> Int:
    if (
        data_addr == 0
        or offsets_addr == 0
        or results_addr == 0
        or status_addr == 0
        or data_n <= 0
        or count <= 0
    ):
        return -1
    var src = BPtr(unsafe_from_address=data_addr)
    var offsets = IPtr(unsafe_from_address=offsets_addr)
    var results = IPtr(unsafe_from_address=results_addr)
    var statuses = IPtr(unsafe_from_address=status_addr)

    for i in range(count):
        var begin = Int(offsets[i])
        var end = Int(offsets[i + 1])
        if begin < 0 or end <= begin or end > data_n:
            return -2
        statuses[i] = Int64(parse_iso(src + begin, end - begin, results + i * 9))
    return 0


@export("mdu_rrule_generate")
def mdu_rrule_generate(
    freq: Int,
    start_ordinal: Int,
    start_seconds: Int,
    interval: Int,
    count_limit: Int,
    until_ordinal: Int,
    until_seconds: Int,
    week_start: Int,
    month_mask: Int,
    monthday_pos: Int,
    monthday_neg: Int,
    weekday_mask: Int,
    nth_addr: Int,
    nth_n: Int,
    hour_mask: Int,
    minute_mask: Int,
    second_mask: Int,
    skip: Int,
    capacity: Int,
    ordinal_addr: Int,
    seconds_addr: Int,
) abi("C") -> Int:
    if (
        nth_addr == 0
        or ordinal_addr == 0
        or seconds_addr == 0
        or nth_n < 0
        or skip < 0
        or capacity <= 0
    ):
        return -1
    var nth_rules = IPtr(unsafe_from_address=nth_addr)
    var result_ordinals = IPtr(unsafe_from_address=ordinal_addr)
    var result_seconds = IPtr(unsafe_from_address=seconds_addr)
    var matched = 0
    var stored = 0
    var start_weekday = (start_ordinal - 1) % 7
    var start_week = start_ordinal - ((start_weekday - week_start + 7) % 7)

    if (
        freq == 1
        and weekday_mask != 0
        and monthday_pos == 0
        and monthday_neg == 0
        and nth_n == 0
    ):
        var start_year = ymd_year_from_ordinal(start_ordinal)
        var start_month = ymd_month_from_ordinal(start_ordinal)
        var month_offset = 0
        while True:
            var month_index = start_month - 1 + month_offset
            var year = start_year + month_index // 12
            if year > 9999:
                return stored
            var month = month_index % 12 + 1
            var first_ordinal = ordinal_from_ymd(year, month, 1)
            if first_ordinal > until_ordinal:
                return stored
            if month_mask == 0 or ((month_mask >> month) & 1) != 0:
                var dim = month_length(year, month)
                var weekday = (first_ordinal - 1) % 7
                for day_offset in range(dim):
                    if ((weekday_mask >> weekday) & 1) != 0:
                        var candidate_ordinal = first_ordinal + day_offset
                        for hour in range(24):
                            if not mask_allows(hour_mask, hour):
                                continue
                            for minute in range(60):
                                if not mask_allows(minute_mask, minute):
                                    continue
                                for second in range(60):
                                    if not mask_allows(second_mask, second):
                                        continue
                                    var seconds = hour * 3600 + minute * 60 + second
                                    if (
                                        candidate_ordinal < start_ordinal
                                        or (
                                            candidate_ordinal == start_ordinal
                                            and seconds < start_seconds
                                        )
                                    ):
                                        continue
                                    if (
                                        candidate_ordinal > until_ordinal
                                        or (
                                            candidate_ordinal == until_ordinal
                                            and seconds > until_seconds
                                        )
                                    ):
                                        return stored
                                    matched += 1
                                    if count_limit >= 0 and matched > count_limit:
                                        return stored
                                    if matched > skip:
                                        if stored >= capacity:
                                            return stored
                                        result_ordinals[stored] = Int64(candidate_ordinal)
                                        result_seconds[stored] = Int64(seconds)
                                        stored += 1
                                    if count_limit >= 0 and matched >= count_limit:
                                        return stored
                    weekday = (weekday + 1) % 7
            month_offset += interval

    if freq == 6:
        var absolute = start_ordinal * 86400 + start_seconds
        var last_absolute = until_ordinal * 86400 + until_seconds
        while absolute <= last_absolute:
            var ordinal = absolute // 86400
            var seconds = absolute - ordinal * 86400
            var year = ymd_year_from_ordinal(ordinal)
            var month = ymd_month_from_ordinal(ordinal)
            var day = ordinal - ordinal_from_ymd(year, month, 1) + 1
            var hour = seconds // 3600
            var minute = (seconds % 3600) // 60
            var second = seconds % 60
            if date_allowed(
                year, month, day, ordinal,
                month_mask, monthday_pos, monthday_neg, weekday_mask, nth_rules, nth_n,
            ) and mask_allows(hour_mask, hour) and mask_allows(minute_mask, minute) and mask_allows(second_mask, second):
                matched += 1
                if count_limit >= 0 and matched > count_limit:
                    break
                if matched > skip:
                    if stored >= capacity:
                        break
                    result_ordinals[stored] = Int64(ordinal)
                    result_seconds[stored] = Int64(seconds)
                    stored += 1
                if count_limit >= 0 and matched >= count_limit:
                    break
            absolute += interval
        return stored

    var ordinal = start_ordinal
    while ordinal <= until_ordinal:
        var year = ymd_year_from_ordinal(ordinal)
        var month = ymd_month_from_ordinal(ordinal)
        var day = ordinal - ordinal_from_ymd(year, month, 1) + 1
        var date_base: Bool
        if freq == 0:
            date_base = (year - Int(ymd_year_from_ordinal(start_ordinal))) % interval == 0
        elif freq == 1:
            var start_year = ymd_year_from_ordinal(start_ordinal)
            var start_month = ymd_month_from_ordinal(start_ordinal)
            var month_diff = (year - start_year) * 12 + month - start_month
            date_base = month_diff >= 0 and month_diff % interval == 0
        elif freq == 2:
            var week_diff = (ordinal - start_week) // 7
            date_base = week_diff >= 0 and week_diff % interval == 0
        elif freq == 3:
            date_base = (ordinal - start_ordinal) % interval == 0
        else:
            date_base = True
        if date_base and date_allowed(
            year, month, day, ordinal, month_mask, monthday_pos, monthday_neg,
            weekday_mask, nth_rules, nth_n,
        ):
            for hour in range(24):
                if not mask_allows(hour_mask, hour):
                    continue
                for minute in range(60):
                    if not mask_allows(minute_mask, minute):
                        continue
                    for second in range(60):
                        if not mask_allows(second_mask, second):
                            continue
                        var seconds = hour * 3600 + minute * 60 + second
                        var time_base = True
                        if freq == 4:
                            var hours = (ordinal - start_ordinal) * 24 + hour - start_seconds // 3600
                            time_base = hours >= 0 and hours % interval == 0
                        elif freq == 5:
                            var minutes = (ordinal - start_ordinal) * 1440 + hour * 60 + minute - start_seconds // 60
                            time_base = minutes >= 0 and minutes % interval == 0
                        if not time_base:
                            continue
                        if ordinal < start_ordinal or (ordinal == start_ordinal and seconds < start_seconds):
                            continue
                        if ordinal > until_ordinal or (ordinal == until_ordinal and seconds > until_seconds):
                            return stored
                        matched += 1
                        if count_limit >= 0 and matched > count_limit:
                            return stored
                        if matched > skip:
                            if stored >= capacity:
                                return stored
                            result_ordinals[stored] = Int64(ordinal)
                            result_seconds[stored] = Int64(seconds)
                            stored += 1
                        if count_limit >= 0 and matched >= count_limit:
                            return stored
        ordinal += 1
    return stored


def ymd_year_from_ordinal(ordinal: Int) -> Int:
    # Duplicated scalar form avoids allocating calendar scratch.
    var z = ordinal - 719163 + 719468
    var era = z // 146097
    var doe = z - era * 146097
    var yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    var year = yoe + era * 400
    var doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    var mp = (5 * doy + 2) // 153
    var month = mp + (3 if mp < 10 else -9)
    return year + (1 if month <= 2 else 0)


def ymd_month_from_ordinal(ordinal: Int) -> Int:
    var z = ordinal - 719163 + 719468
    var era = z // 146097
    var doe = z - era * 146097
    var yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    var doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    var mp = (5 * doy + 2) // 153
    return mp + (3 if mp < 10 else -9)
