using Xunit;
public class Reports {
  [Fact] public void Passes() { Assert.Equal(4, 2 + 2); }
  [Fact] public void Fails() { Assert.Fail("release fixture expected failure"); }
  [Fact(Skip="release fixture skip")] public void Skips() {}
}
