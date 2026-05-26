namespace Game.UI;

using System;
using Game.Core;

public class HUDPanel : BasePanel
{
    private GameManager m_Manager;

    public HUDPanel(GameManager manager)
    {
        m_Manager = manager;
    }

    public void Refresh()
    {
        m_Manager.AddScore(10);
    }

    public override void Show()
    {
        Refresh();
    }
}

public abstract class BasePanel
{
    public bool Visible { get; protected set; }

    public abstract void Show();

    public void Hide()
    {
        Visible = false;
    }
}
