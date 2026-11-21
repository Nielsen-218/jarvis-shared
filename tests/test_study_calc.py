"""core/study_calc: the worked-example numbers are computed by code. Table-driven, exact numbers (checked by hand)."""
import pytest

from core import study_calc as calc


@pytest.mark.parametrize("cidr, mask, network, broadcast, host_bits, total, usable, first, last", [
    ("192.168.1.0/24", "255.255.255.0", "192.168.1.0", "192.168.1.255", 8, 256, 254, "192.168.1.1", "192.168.1.254"),
    ("10.0.0.0/8", "255.0.0.0", "10.0.0.0", "10.255.255.255", 24, 16777216, 16777214, "10.0.0.1", "10.255.255.254"),
    ("172.16.0.0/16", "255.255.0.0", "172.16.0.0", "172.16.255.255", 16, 65536, 65534, "172.16.0.1", "172.16.255.254"),
    ("172.16.5.130/27", "255.255.255.224", "172.16.5.128", "172.16.5.159", 5, 32, 30, "172.16.5.129", "172.16.5.158"),
    ("192.168.1.64/26", "255.255.255.192", "192.168.1.64", "192.168.1.127", 6, 64, 62, "192.168.1.65", "192.168.1.126"),
    ("192.168.1.8/30", "255.255.255.252", "192.168.1.8", "192.168.1.11", 2, 4, 2, "192.168.1.9", "192.168.1.10"),
])
def test_subnet_facts(cidr, mask, network, broadcast, host_bits, total, usable, first, last):
    s = calc.subnet(cidr)
    assert (s["mask"], s["network"], s["broadcast"], s["host_bits"], s["total"], s["usable"], s["first"], s["last"]) == \
        (mask, network, broadcast, host_bits, total, usable, first, last)


@pytest.mark.parametrize("bad", ["", "nonsense", "10.0.0.0/33", "2001:db8::/32"])
def test_subnet_rejects_what_is_not_ipv4_cidr(bad):
    with pytest.raises(ValueError):
        calc.subnet(bad)


@pytest.mark.parametrize("cidr, borrow, networks, prefix, usable", [
    ("192.168.1.0/24", 2, ["192.168.1.0", "192.168.1.64", "192.168.1.128", "192.168.1.192"], 26, 62),
    ("10.0.0.0/24", 1, ["10.0.0.0", "10.0.0.128"], 25, 126),
    ("192.168.0.0/16", 3, [f"192.168.{n}.0" for n in range(0, 256, 32)], 19, 8190),
])
def test_split_gives_equal_subnets(cidr, borrow, networks, prefix, usable):
    kids = calc.split(cidr, borrow)
    assert [k["network"] for k in kids] == networks
    assert {k["prefix"] for k in kids} == {prefix} and {k["usable"] for k in kids} == {usable}


@pytest.mark.parametrize("cidr, borrow", [("192.168.1.0/24", 0), ("192.168.1.0/24", 5), ("192.168.1.0/28", 3)])
def test_split_refuses_a_split_with_no_hosts_left(cidr, borrow):
    with pytest.raises(ValueError):
        calc.split(cidr, borrow)


@pytest.mark.parametrize("n, bits, places", [(0, "0", []), (1, "1", [1]), (13, "1101", [8, 4, 1]), (255, "11111111", [128, 64, 32, 16, 8, 4, 2, 1]),
                                             (256, "100000000", [256]), (170, "10101010", [128, 32, 8, 2])])
def test_binary_conversion(n, bits, places):
    b = calc.binary(n)
    assert (b["bits"], b["places"]) == (bits, places)
    assert calc.from_binary(bits) == n
    assert b["sum"] == (" + ".join(map(str, places)) or "0")


@pytest.mark.parametrize("bad", [-1, 65536])
def test_binary_range(bad):
    with pytest.raises(ValueError):
        calc.binary(bad)


@pytest.mark.parametrize("bits", ["", "102", "1" * 17, "abc"])
def test_from_binary_rejects(bits):
    with pytest.raises(ValueError):
        calc.from_binary(bits)


@pytest.mark.parametrize("n, log, nlogn, square", [(2, 2, 2, 4), (8, 4, 24, 64), (1000, 10, 10000, 1000000),
                                                   (1000000, 20, 20000000, 1000000000000)])
def test_bigo_counts(n, log, nlogn, square):
    c = calc.bigo(n)
    assert (c["O(1)"], c["O(log n)"], c["O(n)"], c["O(n log n)"], c["O(n^2)"]) == (1, log, n, nlogn, square)


def test_bigo_range():
    for bad in (0, 1, 10 ** 10):
        with pytest.raises(ValueError):
            calc.bigo(bad)


@pytest.mark.parametrize("level, disks, size, raw, usable, survives", [
    (0, 2, 4, 8, 8, 0), (0, 3, 1, 3, 3, 0), (1, 2, 4, 8, 4, 1), (1, 3, 2, 6, 2, 2), (5, 4, 2, 8, 6, 1), (5, 3, 4, 12, 8, 1),
    (6, 6, 2, 12, 8, 2), (10, 4, 2, 8, 4, 1), (10, 6, 1, 6, 3, 1)])
def test_raid_capacity(level, disks, size, raw, usable, survives):
    r = calc.raid(level, disks, size)
    assert (r["raw"], r["usable"], r["survives"]) == (raw, usable, survives)


@pytest.mark.parametrize("level, disks, size", [(5, 2, 2), (2, 4, 2), (6, 3, 2), (10, 5, 2), (1, 1, 2), (0, 2, 0)])
def test_raid_rejects_an_invalid_array(level, disks, size):
    with pytest.raises(ValueError):
        calc.raid(level, disks, size)


@pytest.mark.parametrize("value, octal, symbolic, parts", [
    ("754", "754", "rwxr-xr--", ["4+2+1", "4+1", "4"]), ("rwxr-xr--", "754", "rwxr-xr--", ["4+2+1", "4+1", "4"]),
    ("644", "644", "rw-r--r--", ["4+2", "4", "4"]), ("777", "777", "rwxrwxrwx", ["4+2+1"] * 3),
    ("000", "000", "---------", ["0", "0", "0"]), ("rw-------", "600", "rw-------", ["4+2", "0", "0"]), ("--x-w-r--", "124", "--x-w-r--", ["1", "2", "4"])])
def test_permissions_both_ways(value, octal, symbolic, parts):
    p = calc.perms(value)
    assert (p["octal"], p["symbolic"], p["parts"]) == (octal, symbolic, parts)


@pytest.mark.parametrize("bad", ["", "888", "75", "rwxr-xr-", "abcdefghi"])
def test_permissions_reject(bad):
    with pytest.raises(ValueError):
        calc.perms(bad)


@pytest.mark.parametrize("n, result", [(0, 1), (1, 1), (4, 24), (5, 120), (8, 40320)])
def test_factorial_chain(n, result):
    f = calc.factorial(n)
    assert f["result"] == result and f["down"][-1] == "factorial(0) = 1"
    assert len(f["down"]) == n + 1 and (f["up"][-1] == f"factorial({n}) = {result}" if n else f["up"] == [])


def test_factorial_four_is_spelled_out():
    f = calc.factorial(4)
    assert f["down"] == ["factorial(4) = 4 x factorial(3)", "factorial(3) = 3 x factorial(2)", "factorial(2) = 2 x factorial(1)",
                         "factorial(1) = 1 x factorial(0)", "factorial(0) = 1"]
    assert f["up"] == ["factorial(1) = 1", "factorial(2) = 2", "factorial(3) = 6", "factorial(4) = 24"]


@pytest.mark.parametrize("topic, kind", [
    ("Subnetting", "subnet"), ("CIDR notation", "subnet"), ("Binary numbers", "binary"), ("Big-O notation", "bigo"), ("time complexity", "bigo"),
    ("RAID", "raid"), ("File permissions", "perms"), ("chmod", "perms"), ("Recursion", "recursion"), ("factorial", "recursion"),
    ("DNS", ""), ("Public-key cryptography", ""), ("", ""), ("Binary search tree", ""), ("Hashing vs encryption", "")])
def test_which_topics_have_a_worked_example(topic, kind):
    assert calc.kind(topic) == kind


def test_example_defaults_and_the_numbers_in_the_facts():
    sub = calc.example("Subnetting", "")
    assert sub["parent"]["cidr"] == "192.168.1.0/24" and len(sub["subnets"]) == 4
    assert "256 addresses, 254 usable" in sub["facts"][0] and "192.168.1.0-63, 192.168.1.64-127, 192.168.1.128-191, 192.168.1.192-255" in sub["facts"][1]
    assert "O(log n) = 20 steps" in calc.example("Big-O notation", "")["facts"][0]
    assert "usable 6 TB, survives 1 failed disk" in calc.example("RAID", "")["facts"][0]
    assert calc.example("File permissions", "")["perms"]["octal"] == "754"
    assert calc.example("Recursion", "")["fact"]["result"] == 24
    assert calc.example("Binary numbers", "")["binary"]["bits"] == "1101"
    assert calc.example("DNS", "anything") is None


def test_example_takes_the_numbers_from_the_students_passage():
    sub = calc.example("Subnetting", "Take 10.1.0.0/16 and borrowing three bits gives eight subnets.")
    assert sub["parent"]["cidr"] == "10.1.0.0/16" and sub["borrow"] == 3 and sub["subnets"][1]["network"] == "10.1.32.0"
    raid = calc.example("RAID 6", "RAID 6 with 6 disks of 4 TB.")["raid"]
    assert (raid["level"], raid["usable"], raid["survives"]) == (6, 16, 2)
    assert calc.example("RAID", "RAID 1 mirrors two disks.")["raid"]["usable"] == 2
    assert calc.example("File permissions", "so 640 means the group can only read")["perms"]["symbolic"] == "rw-r-----"
    assert calc.example("Recursion", "factorial(5) is 120")["fact"]["result"] == 120
    assert calc.example("Binary numbers", "the number 202 is 11001010")["binary"]["bits"] == "11001010"


def test_example_falls_back_when_the_passage_numbers_are_unusable():
    bad = calc.example("Subnetting", "A /29 network 10.0.0.0/29 borrowing 4 bits")      # no room left to split
    assert bad["parent"]["cidr"] == "192.168.1.0/24" and bad["borrow"] == 2
    assert calc.example("RAID", "RAID 5 with 2 disks of 2 TB") is None                  # an impossible array: no example, no wrong number


# ---- review fixes: a topic that only shares a word with a worked example gets no example -----------------------------------
@pytest.mark.parametrize("topic", [
    "Recursive DNS resolution", "recursive DNS resolver", "Recursive queries", "Recursion in DNS",
    "NTFS permissions", "Android permissions", "IAM permissions", "Windows file permissions", "Cyclomatic complexity",
    "Computational complexity", "Complexity of Kubernetes"])
def test_a_topic_that_only_shares_a_word_gets_no_worked_example(topic):
    assert calc.kind(topic) == "" and calc.example(topic, "anything about it") is None


@pytest.mark.parametrize("topic, kind", [
    ("Unix permissions", "perms"), ("Linux file permissions", "perms"), ("Recursive functions", "recursion"), ("Recursion in programming", "recursion"),
    ("Algorithm complexity", "bigo"), ("Big O", "bigo"), ("Time complexity of sorting", "bigo")])
def test_the_real_topics_keep_their_worked_example(topic, kind):
    assert calc.kind(topic) == kind


def test_an_ambiguous_permissions_topic_needs_the_passage_to_be_about_unix():
    assert calc.kind("File permissions") == "perms"                               # the plain, classic name
    assert calc.kind("Folder permissions", "Give the group read access with chmod 640.") == "perms"
    assert calc.kind("Folder permissions", "Right-click the share and open the security tab.") == ""


def test_the_level_comes_from_the_topic_before_the_passage():
    raid = calc.example("RAID 6", "RAID 5 uses single parity. RAID 6 uses double parity.")["raid"]
    assert (raid["level"], raid["usable"], raid["survives"]) == (6, 4, 2)
    raid = calc.example("RAID 10", "RAID 5 stripes with parity. RAID 10 mirrors then stripes.")["raid"]
    assert (raid["level"], raid["usable"]) == (10, 4)
    assert calc.example("RAID-6", "RAID 5 first.")["raid"]["level"] == 6
    # no level in the topic: the level the passage talks about most, not whichever it names first
    text = "RAID 0 has no redundancy. RAID 5 uses parity, and RAID 5 survives one failed disk. RAID 5 is common."
    assert calc.example("RAID", text)["raid"]["level"] == 5


def test_a_number_is_taken_from_the_topic_or_from_a_binary_context_never_from_any_digits_in_the_passage():
    assert calc.example("Binary numbers", "In 2024 we learned binary.")["binary"]["bits"] == "1101"          # the default, not 2024
    assert calc.example("Binary numbers", "Convert 45 to binary.")["binary"]["bits"] == "101101"
    assert calc.example("Binary 25", "In 2024 we learned binary.")["binary"]["bits"] == "11001"
    assert calc.example("Binary numbers", "7 in binary is 111.")["binary"]["bits"] == "111"
    assert calc.example("Subnetting 10.2.0.0/16", "Take 172.16.0.0/12.")["parent"]["cidr"] == "10.2.0.0/16"
    assert calc.example("chmod 640", "so 754 is common")["perms"]["octal"] == "640"
    assert calc.example("Factorial(5)", "factorial(3) is 6")["fact"]["result"] == 120
