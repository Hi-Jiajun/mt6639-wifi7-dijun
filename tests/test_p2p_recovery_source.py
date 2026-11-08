"""Source-level guards for P2P partial recovery safety boundaries."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "tools/p2p_once.sh").read_text(encoding="utf-8")
START = SOURCE.index("recover() {")
END = SOURCE.index('\n}\ncase "$OP" in', START)
RECOVER = SOURCE[START:END]


class RecoverySourceTests(unittest.TestCase):
    def test_unknown_group_does_not_abort_other_resource_recovery(self):
        self.assertIn("group_state=foreign_or_unknown", RECOVER)
        self.assertIn("group_state=query_failed", RECOVER)
        self.assertIn("continuing independent checks", RECOVER)
        self.assertNotIn("return 73", RECOVER)
        self.assertIn('cmp -s "$D/group.active" "$D/group.recover"', RECOVER)
        self.assertIn("group_state=remove_pending", RECOVER)
        self.assertIn('"$D/p2p_eht_profile.sh" "$D" restore', RECOVER)
        self.assertIn('"$D/twt_recover.sh" check', RECOVER)

    def test_unresolved_group_blocks_global_eht_restore(self):
        gate = RECOVER.index('if [ "$p2p_idle_verified" = 1 ]; then')
        restore = RECOVER.index('"$D/p2p_eht_profile.sh" "$D" restore')
        self.assertLess(gate, restore)
        self.assertIn("eht_state=unresolved_p2p_group", RECOVER)

    def test_unresolved_group_blocks_station_reselection_but_allows_read_only_check(self):
        check = RECOVER.index('"$D/twt_recover.sh" check')
        select = RECOVER.index('"$D/twt_recover.sh" select')
        gate = RECOVER.index('if [ "$group_safe" = 1 ] && [ "$journal_ok" = 1 ]; then', check)
        self.assertLess(check, gate)
        self.assertLess(gate, select)
        self.assertIn("station_state=deferred_p2p_unresolved", RECOVER)

    def test_recovery_state_is_atomically_persisted_per_stage(self):
        self.assertIn('mv "$D/recovery.resources.tmp" "$D/recovery.resources"', RECOVER)
        self.assertGreaterEqual(RECOVER.count("record_recovery_state"), 8)
        self.assertIn('printf \'%s\\n\' "$rc" > "$D/recovery.rc"', RECOVER)
        self.assertLess(RECOVER.index("group_state=remove_pending"),
                        RECOVER.index('p2p remove-group >'))
        self.assertLess(RECOVER.index("eht_state=restore_pending"),
                        RECOVER.index('"$D/p2p_eht_profile.sh" "$D" restore'))
        self.assertLess(RECOVER.index("station_state=selection_pending"),
                        RECOVER.index('"$D/twt_recover.sh" select'))

    def test_go_frequency_is_carried_through_create_and_recovery(self):
        self.assertIn("REQUESTED_FREQ=${3:-}", SOURCE)
        self.assertIn("6135) SSID_BASE=DIRECT-mt-Dijun-6G", SOURCE)
        self.assertIn('"$D/go.freq"', SOURCE)
        self.assertIn('p2p create-group-with-config "$SSID" "$pass" "$GO_FREQ" false', SOURCE)
        self.assertIn('frequency:[[:space:]]*$GO_FREQ', SOURCE)

    def test_unique_owner_intent_closes_presnapshot_creation_window(self):
        intent = SOURCE.index('printf \'ssid=%s\\nfrequency=%s\\n\'')
        create = SOURCE.index('p2p create-group-with-config "$SSID" "$pass" "$GO_FREQ" false')
        self.assertLess(intent, create)
        self.assertIn('$D/owner.intent.tmp', SOURCE)
        self.assertIn('load_owner_intent && SSID=$OWNER_SSID', RECOVER)
        self.assertIn('"$D/connection.recover"', RECOVER)
        self.assertIn('intent_owned=1', RECOVER)
        self.assertIn('snapshot_owned=1', RECOVER)


if __name__ == "__main__":
    unittest.main()
