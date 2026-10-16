"""Reader quality is a conservative projection, never source or model-text repair."""
import copy
import unittest

from hoomy.project_state_quality import filter_output, filter_model_output, item_reasons, model_quote_records, POLICY_HASH


def record(text, message="m", start=0):
    return {"text": text, "message_id": message, "char_start": start, "char_end": start + len(text)}


def assertion(text="A historical report recorded version v0.2.0.", refs=None):
    return {"key": "report", "text": text, "limitations": ["This is historical."], "source_refs": refs or [{"passage_id": "p0001_01"}]}


class QualityTests(unittest.TestCase):
    def test_complete_prose_closed_code_and_own_version_are_preserved(self):
        text="The assistant reported `v0.2.0`."
        item=assertion(text)
        self.assertEqual(item_reasons(item,[record("Installed 0.2.0.")]),[])
        self.assertEqual(item_reasons(assertion("A report ended with ‘done.’"),[record("Report.")]),[])
        self.assertEqual(item_reasons(assertion("A literal escaped \\` is recorded."),[record("Report.")]),[])

    def test_three_defect_classes_do_not_repair_or_borrow_other_items_quotes(self):
        item=assertion("The reported release was `v0.4.0 friend_b")
        self.assertEqual(item_reasons(item,[record("Release 0.4.0 friend beta.")]),["nonterminal_prose","unclosed_code"])
        item=assertion("The historical installation was v0.2.0.")
        self.assertEqual(item_reasons(item,[record("Release 0.4.0.")]),["unsupported_version"])
        output={"assertions":[item,assertion("A historical report recorded v0.2.0.",[{"passage_id":"p0002_01"}])],"uncertainties":[],"contradictions":[]}
        before=copy.deepcopy(output)
        visible,report=filter_model_output(output,{"p0001_01":record("Release 0.4.0."),"p0002_01":record("Installed v0.2.0.","other")})
        self.assertEqual(len(visible["assertions"]),1)
        self.assertEqual(report["excluded_items"],[{"section":"assertions","index":0,"reasons":["unsupported_version"]}])
        self.assertEqual(output,before)
        self.assertEqual(report["policy_hash"],POLICY_HASH)

    def test_own_adjacent_frozen_quotes_complete_version_but_other_messages_or_gaps_cannot(self):
        item=assertion()
        self.assertEqual(item_reasons(item,[record("Installed v0.",start=0),record("2.0.",start=13)]),[])
        self.assertEqual(item_reasons(item,[record("Installed v0.",start=0),record("2.0.",message="other",start=13)]),["unsupported_version"])
        self.assertEqual(item_reasons(item,[record("Installed v0.",start=0),record("2.0.",start=14)]),["unsupported_version"])
        payload={"passages":[{"passage_id":"p0001_01","text":"x"*109+" v0"},{"passage_id":"p0001_02","text":".2.0."}]}
        records=model_quote_records(payload)
        item=assertion(refs=[{"passage_id":"p0001_01"},{"passage_id":"p0001_02"}])
        visible,_=filter_model_output({"assertions":[item],"uncertainties":[],"contradictions":[]},records)
        self.assertEqual(visible["assertions"],[item])

    def test_dependent_contradictions_disappear_without_rewriting_surviving_text(self):
        good=assertion("A historical report was recorded.");bad={**assertion("A report and explicit"),"key":"bad"}
        output={"assertions":[good,bad],"uncertainties":[],"contradictions":[{"assertion_keys":["report","bad"],"text":"Conflicting reports."}]}
        visible,report=filter_output(output,lambda item:[record("Historical report.")])
        self.assertIs(visible["assertions"][0],good)
        self.assertEqual(visible["contradictions"],[])
        self.assertEqual(output["contradictions"][0]["text"],"Conflicting reports.")
        self.assertEqual(report["visible_assertions"],1)

    def test_conservative_rule_is_not_a_semantic_verifier(self):
        # Lexical acceptance cannot establish this statement's truth.
        self.assertEqual(item_reasons(assertion("A fabricated claim is true."),[record("Unrelated text.")]),[])
        self.assertEqual(item_reasons(assertion("A valid sentence without a period"),[record("Report.")]),["nonterminal_prose"])
