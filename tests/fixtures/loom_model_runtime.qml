import QtQuick
import QtQml.Models

Item {
    id: root
    width: 200; height: 200
    property int creations: 0
    property int childCreations: 0
    property bool passed: false
    property string errorText: ""
    property ListModel uiViewRows: ListModel { dynamicRoles: true }
    function canonical(value) {
        if (Array.isArray(value)) return value.map(item => canonical(item));
        if (value && typeof value === "object") {
            const sorted = {};
            for (const key of Object.keys(value).sort()) sorted[key] = canonical(value[key]);
            return sorted;
        }
        return value;
    }
    function sig(value) { return JSON.stringify(canonical(value)); }
    function sync(views) {
        for (let i=0;i<views.length;i++) {
            let found = -1;
            for(let j=i;j<uiViewRows.count;j++)
                if (uiViewRows.get(j).view.id === views[i].id) { found=j; break; }
            if(found<0){uiViewRows.append({view:views[i]});found=uiViewRows.count-1;}
            if(found!==i)uiViewRows.move(found,i,1);
            if(sig(uiViewRows.get(i).view)!==sig(views[i]))uiViewRows.setProperty(i,"view",views[i]);
        }
        if(uiViewRows.count>views.length)uiViewRows.remove(views.length,uiViewRows.count-views.length);
    }
    Repeater {
        model: root.uiViewRows
        delegate: Item {
            required property var view
            readonly property var kids: Array.isArray(view.root.children) ? view.root.children : []
            property ListModel childRows: ListModel { dynamicRoles: true }
            Component.onCompleted: { root.creations++; syncChildren(); }
            onKidsChanged: syncChildren()
            function syncChildren() {
                for(let i=0;i<kids.length;i++) {
                    let found=-1;
                    for(let j=i;j<childRows.count;j++)
                        if(childRows.get(j).itemNode.id===kids[i].id){found=j;break;}
                    if(found<0){childRows.append({itemNode:kids[i]});found=childRows.count-1;}
                    if(found!==i)childRows.move(found,i,1);
                    if(root.sig(childRows.get(i).itemNode)!==root.sig(kids[i]))
                        childRows.setProperty(i,"itemNode",kids[i]);
                }
                if(childRows.count>kids.length)childRows.remove(kids.length,childRows.count-kids.length);
            }
            Repeater {
                model: childRows
                delegate: Item {
                    required property var itemNode
                    Component.onCompleted: root.childCreations++
                }
            }
        }
    }
    Component.onCompleted: {
        const old = {id:"v",root:{children:[{id:"a",props:{text:"a"}},{id:"b",props:{text:"b"}}]}};
        sync([old]);
        if (creations!==1 || childCreations!==2) errorText += "initial " + creations + "/" + childCreations+";";
        sync([{id:"v",root:{children:[{id:"a",props:{text:"a"}},{id:"b",props:{text:"new"}}]}}]);
        if (creations!==1 || childCreations!==2) errorText += "update " + creations + "/" + childCreations+";";
        sync([{id:"v",root:{children:[{id:"b",props:{text:"new"}},{id:"a",props:{text:"a"}}]}}]);
        if (creations!==1 || childCreations!==2) errorText += "reorder " + creations + "/" + childCreations+";";
        passed = errorText === "";
        console.log("LOOM_QML_MODEL_RUNTIME_RESULT", passed, errorText || "stable delegates", creations, childCreations);
        Qt.quit();
    }
}
