from sauti_server.text import dictionary, guard, rules


def test_rules_remove_hesitations_and_stutters():
    assert rules.clean("um so i i think uh we should go", "en") == "So I think we should go"
    assert rules.clean("the the meeting is at 3 pm , not 4", "en") == "The meeting is at 3 pm, not 4"


def test_rules_keep_meaningful_words():
    # "haa" means yes in Somali; "like" can carry meaning in English.
    assert "haa" in rules.clean("haa waan imanayaa", "so").lower()
    assert "like" in rules.clean("I like the plan", "en")


def test_rules_arabic_elongated_hesitation():
    out = rules.clean("اممم الاجتماع غدا", "ar")
    assert "اممم" not in out and "الاجتماع غدا" in out


def test_aliases_replace_whole_words_only():
    entries = [{"term": "Hargeisa", "aliases": ["hargaysa"]}, {"term": "Proxmox", "aliases": ["prox mocks"]}]
    assert dictionary.apply_aliases("fly to hargaysa via prox mocks", entries) == "fly to Hargeisa via Proxmox"
    assert dictionary.apply_aliases("hargaysaville", entries) == "hargaysaville"


def test_merge_personal_overrides_org():
    merged = dictionary.merge([{"term": "Sauti", "aliases": ["sow tea"]}], [{"term": "sauti", "aliases": []}])
    assert merged == [{"term": "Sauti", "aliases": ["sow tea"]}]


def test_guard_accepts_faithful_cleanup():
    src = "So I think we should meet on Tuesday at 3 pm not Wednesday"
    out = "So I think we should meet on Tuesday at 3 pm, not Wednesday."
    assert guard.check_cleanup(src, out, "en", []).ok


def test_guard_rejects_dropped_negation():
    r = guard.check_cleanup("I do not want the blue one", "I want the blue one.", "en", [])
    assert not r.ok and "negation_changed" in r.reasons


def test_guard_rejects_changed_number():
    r = guard.check_cleanup("send 250 units to the warehouse", "Send 205 units to the warehouse.", "en", [])
    assert "numbers_changed" in r.reasons


def test_guard_accepts_number_words_to_digits():
    assert guard.check_cleanup("we need twenty five chairs by twenty twenty six", "We need 25 chairs by 2026.",
                               "en", []).ok


def test_guard_rejects_summary_and_invention():
    src = "please call the supplier tomorrow morning and ask whether the delivery of the new cables can be moved"
    assert "too_short" in guard.check_cleanup(src, "Call the supplier.", "en", []).reasons
    inv = guard.check_cleanup("call the supplier tomorrow", "Call the supplier tomorrow and cancel every contract.",
                              "en", [])
    assert not inv.ok


def test_guard_rejects_dropped_name_and_term():
    r = guard.check_cleanup("ask Amina to update the Proxmox host", "Ask her to update the host.", "en", ["Proxmox"])
    assert "name_dropped" in r.reasons and "term_dropped" in r.reasons


def test_guard_rejects_answering_the_transcript():
    r = guard.check_cleanup("what is the capital of Kenya", "The capital of Kenya is Nairobi.", "en", [])
    assert not r.ok


def test_guard_arabic_digits_normalised():
    assert guard.check_cleanup("الاجتماع الساعة ٣ غدا", "الاجتماع الساعة 3 غدا.", "ar", []).ok


def test_translation_check():
    assert guard.check_translation("Mashine 3 zimeharibika leo asubuhi.", "3 machines broke down this morning.").ok
    assert "numbers_changed" in guard.check_translation("Tuna watu 12 hapa", "We have 21 people here").reasons
    same = "Hii ni sentensi ndefu ambayo haikutafsiriwa"
    assert "untranslated" in guard.check_translation(same, same).reasons
