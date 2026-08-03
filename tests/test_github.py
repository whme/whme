from readme_updater.github import public_commits, public_query


class TestPrivateReposNeverLeak:
    def test_every_search_query_is_restricted_to_public_repos(self) -> None:
        assert "is:public" in public_query()
        assert (
            public_query("type:pr repo:x/y") == "author:whme is:public type:pr repo:x/y"
        )

    def test_commits_from_private_repos_are_dropped(self) -> None:
        public = {"repository": {"full_name": "whme/csshw", "private": False}}
        private = {"repository": {"full_name": "whme/secret", "private": True}}
        assert public_commits([public, private]) == [public]
