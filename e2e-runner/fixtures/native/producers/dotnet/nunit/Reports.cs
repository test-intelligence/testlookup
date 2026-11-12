using NUnit.Framework;
public class Reports {
  [Test] public void Passes() { Assert.That(2 + 2, Is.EqualTo(4)); }
  [Test] public void Fails() { Assert.Fail("release fixture expected failure"); }
  [Test, Ignore("release fixture skip")] public void Skips() {}
}
