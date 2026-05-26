using System;

namespace Game.Data
{
    public class Container
    {
        public class Inner
        {
            public int Value { get; set; }

            public void Reset()
            {
                Value = 0;
            }
        }

        public Inner Item { get; }

        public Container()
        {
            Item = new Inner();
        }
    }
}
